"""Live session manager — orchestrates STT, TTS, and response handling."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

from libs.common import get_logger
from libs.common.auth import create_internal_transaction_token
from libs.db.models import (
    ChatMessage as ChatMessageModel,
    Conversation,
    Job,
    JobStatus,
    LiveSession,
    LiveSessionStatus,
    MessageRole,
)
from libs.db.session import get_session_context
from libs.messaging.kafka import get_producer
from libs.messaging.redis import RedisPubSub, get_redis_client

from ..config import get_config
from ..stt.deepgram_client import DeepgramSTTClient
from ..tts.elevenlabs_client import ElevenLabsTTSClient
from ..vision.frame_processor import FrameProcessor
import time
import os
import json
from .state import LiveSessionData, SessionState

logger = get_logger(__name__)


class SessionManager:
    """Manages active live sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSessionData] = {}
        self._stt_clients: dict[str, DeepgramSTTClient] = {}
        self._tts_clients: dict[str, ElevenLabsTTSClient] = {}
        self._frame_processor = FrameProcessor()

    # #region agent log helper
    def _write_debug(self, message: str, data: dict, hypothesis_id: str = "STATE") -> None:
        try:
            payload = {
                "id": f"log_{int(time.time()*1000)}",
                "timestamp": int(time.time() * 1000),
                "location": "session.manager",
                "message": message,
                "data": data,
                "hypothesisId": hypothesis_id,
            }
            paths = [".cursor/debug.log", os.path.join(os.getcwd(), ".cursor", "debug.log")]
            for path in paths:
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(payload) + "\n")
                    break
                except OSError:
                    continue
        except Exception:
            pass
    # #endregion

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def get_session(self, session_id: str) -> LiveSessionData | None:
        return self._sessions.get(session_id)

    async def start_session(self, session_data: dict) -> LiveSessionData:
        """Start a new live session with STT/TTS pipelines."""
        config = get_config()
        session_id = session_data["session_id"]

        if not (config.deepgram_api_key or "").strip():
            redis = await get_redis_client()
            await redis.publish(
                f"live_session:{session_id}",
                json.dumps({
                    "type": "error",
                    "message": "Voice assistant is not configured: DEEPGRAM_API_KEY is missing. Set it in your environment or .env.",
                }),
            )
            logger.error(
                "Cannot start live session: DEEPGRAM_API_KEY is not set",
                session_id=session_id,
            )
            raise ValueError("DEEPGRAM_API_KEY is required for live sessions")

        session = LiveSessionData(
            session_id=session_id,
            tenant_id=session_data["tenant_id"],
            user_id=session_data.get("user_id"),
            partner_id=session_data.get("partner_id"),
            conversation_id=session_data.get("conversation_id"),
            language=session_data.get("language", "en"),
            tts_voice_id=session_data.get("tts_voice_id"),
        )
        self._sessions[session_id] = session

        # Persist to DB
        await self._persist_session_start(session)

        # Initialize STT client
        stt_client = DeepgramSTTClient(
            api_key=config.deepgram_api_key,
            model=config.deepgram_model,
            language=session.language,
            sample_rate=config.deepgram_sample_rate,
            encoding=config.deepgram_encoding,
            endpointing=config.deepgram_endpointing,
        )
        self._stt_clients[session_id] = stt_client

        # Initialize TTS client
        tts_client = ElevenLabsTTSClient(
            api_key=config.elevenlabs_api_key,
            voice_id=session.tts_voice_id or config.elevenlabs_default_voice_id,
            model=config.elevenlabs_model,
        )
        self._tts_clients[session_id] = tts_client

        # Start STT connection (callback runs in Deepgram's worker thread;
        # schedule coroutine on main loop via run_coroutine_threadsafe)
        main_loop = asyncio.get_running_loop()

        def on_transcript(text: str, is_final: bool) -> None:
            asyncio.run_coroutine_threadsafe(
                self._on_transcript(session_id, text, is_final),
                main_loop,
            )

        await stt_client.connect(on_transcript=on_transcript)

        # Start listening for audio input from Redis
        session.stt_task = asyncio.create_task(
            self._listen_audio_input(session_id)
        )

        # Start listening for orchestrator responses
        session.response_task = asyncio.create_task(
            self._listen_responses(session_id)
        )

        # Start session timeout
        session.timeout_task = asyncio.create_task(
            self._session_timeout(session_id, config.live_session_timeout_seconds)
        )

        session.state = SessionState.LISTENING
        await self._publish_status(session_id, "listening")

        logger.info(
            "Live session started",
            session_id=session_id,
            tenant_id=session.tenant_id,
        )

        return session

    async def end_session(self, session_id: str) -> None:
        """End a live session and clean up resources."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        session.state = SessionState.ENDED

        # Cancel background tasks
        for task in [session.stt_task, session.tts_task, session.response_task, session.timeout_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close STT/TTS connections
        stt_client = self._stt_clients.pop(session_id, None)
        if stt_client:
            await stt_client.disconnect()

        tts_client = self._tts_clients.pop(session_id, None)
        if tts_client:
            await tts_client.disconnect()

        # Persist final state to DB
        await self._persist_session_end(session)

        # Clean up Redis
        redis = await get_redis_client()
        await redis.delete(f"live_session:{session_id}")

        await self._publish_status(session_id, "ended")

        logger.info(
            "Live session ended",
            session_id=session_id,
            total_turns=session.total_turns,
            audio_input_seconds=session.audio_input_seconds,
            audio_output_seconds=session.audio_output_seconds,
        )

    async def pause_session(self, session_id: str) -> None:
        """Pause a live session (stop STT but keep connection)."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.state = SessionState.PAUSED
        stt_client = self._stt_clients.get(session_id)
        if stt_client:
            await stt_client.pause()

        await self._publish_status(session_id, "paused")

    async def resume_session(self, session_id: str) -> None:
        """Resume a paused live session."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.state = SessionState.LISTENING
        stt_client = self._stt_clients.get(session_id)
        if stt_client:
            await stt_client.resume()

        await self._publish_status(session_id, "listening")

    async def interrupt_session(self, session_id: str) -> None:
        """Interrupt current TTS output (user started speaking)."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.tts_interrupted = True

        # Cancel TTS task
        if session.tts_task and not session.tts_task.done():
            session.tts_task.cancel()
            try:
                await session.tts_task
            except asyncio.CancelledError:
                pass

        tts_client = self._tts_clients.get(session_id)
        if tts_client:
            await tts_client.flush()

        session.state = SessionState.LISTENING
        await self._publish_status(session_id, "listening")

    async def _on_transcript(
        self, session_id: str, text: str, is_final: bool
    ) -> None:
        """Called when Deepgram produces a transcript."""
        session = self._sessions.get(session_id)
        if not session:
            return

        # Publish transcript to client via Redis
        redis = await get_redis_client()
        await redis.publish(
            f"live_session:{session_id}",
            json.dumps({
                "type": "transcript",
                "text": text,
                "is_final": is_final,
            }),
        )

        if is_final and text.strip():
            # Create a new job for this voice turn
            session.total_turns += 1
            session.state = SessionState.PROCESSING
            await self._publish_status(session_id, "thinking")
            await self._create_job_from_transcript(session, text)

    async def _create_job_from_transcript(
        self, session: LiveSessionData, text: str
    ) -> None:
        """Create a Kafka job from a voice transcript (same as chat endpoint)."""
        config = get_config()
        job_id = uuid.uuid4()
        session.current_job_id = str(job_id)

        tenant_id = uuid.UUID(session.tenant_id)
        user_id = uuid.UUID(session.user_id) if session.user_id else None
        partner_id = uuid.UUID(session.partner_id) if session.partner_id else None

        # Handle conversation: reuse or create
        conversation_id_val: uuid.UUID | None = None
        if session.conversation_id:
            conversation_id_val = uuid.UUID(session.conversation_id)
        else:
            async with get_session_context() as db_session:
                title = text[:80].rsplit(" ", 1)[0] + "..." if len(text) > 80 else text
                conv = Conversation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    title=title.strip(),
                )
                db_session.add(conv)
                await db_session.flush()
                conversation_id_val = conv.id
                session.conversation_id = str(conversation_id_val)

        # Persist job + message to DB
        async with get_session_context() as db_session:
            job = Job(
                id=job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id_val,
                status=JobStatus.PENDING,
                provider=config.default_llm_provider,
                model_id=(
                    config.anthropic_default_model
                    if config.default_llm_provider == "anthropic"
                    else config.openai_default_model
                ),
                metadata_={"source": "live_voice", "session_id": session.session_id},
            )
            db_session.add(job)

            msg = ChatMessageModel(
                job_id=job_id,
                sequence_num=0,
                role=MessageRole.USER,
                content=text,
            )
            db_session.add(msg)
            await db_session.flush()

        # Generate internal token
        internal_token = create_internal_transaction_token(
            job_id=job_id,
            tenant_id=tenant_id,
            credit_check_passed=True,
            max_tokens=4096,
            partner_id=partner_id,
        )

        # Publish to Kafka
        job_payload = {
            "job_id": str(job_id),
            "tenant_id": session.tenant_id,
            "partner_id": session.partner_id,
            "user_id": session.user_id,
            "provider": config.default_llm_provider,
            "model": (
                config.anthropic_default_model
                if config.default_llm_provider == "anthropic"
                else config.openai_default_model
            ),
            "messages": [{"role": "user", "content": text}],
            "system": None,
            "tools": None,
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": True,
            "metadata": {
                "source": "live_voice",
                "session_id": session.session_id,
            },
        }

        producer = await get_producer()
        await producer.send(
            topic=config.jobs_topic,
            message=job_payload,
            key=session.tenant_id,
            headers={
                "job_id": str(job_id),
                "tenant_id": session.tenant_id,
                "partner_id": session.partner_id or "",
                "internal_token": "present",
            },
        )

        logger.info(
            "Voice job published",
            session_id=session.session_id,
            job_id=str(job_id),
            transcript_length=len(text),
        )

    async def _listen_audio_input(self, session_id: str) -> None:
        """Listen for audio chunks from Redis and feed to STT."""
        pubsub = RedisPubSub()
        await pubsub.connect()
        channel = f"live_session:{session_id}:audio_in"
        await pubsub.subscribe(channel)
        logger.debug("Listening for audio input", session_id=session_id, channel=channel)

        stt_client = self._stt_clients.get(session_id)
        if not stt_client:
            logger.error("No STT client for session", session_id=session_id)
            return

        try:
            async for _channel, data in pubsub.listen():
                session = self._sessions.get(session_id)
                if not session or session.state == SessionState.ENDED:
                    break

                if session.state == SessionState.PAUSED:
                    continue

                # If user is sending audio while TTS is playing, interrupt
                if session.state == SessionState.SPEAKING:
                    await self.interrupt_session(session_id)

                audio_data = data.get("data", "")
                if audio_data:
                    # Instrument audio in events
                    try:
                        self._write_debug(
                            "Audio received from client",
                            {"session_id": session_id, "data_len": len(audio_data), "state": session.state},
                            "AUDIO",
                        )
                    except Exception:
                        pass
                    logger.debug("Sending audio to STT", session_id=session_id, data_len=len(audio_data))
                    await stt_client.send_audio(audio_data)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.disconnect()

    async def _listen_responses(self, session_id: str) -> None:
        """Listen for orchestrator responses and stream through TTS."""
        session = self._sessions.get(session_id)
        if not session:
            return

        pubsub = RedisPubSub()
        await pubsub.connect()

        try:
            while session.state != SessionState.ENDED:
                job_id = session.current_job_id
                if not job_id:
                    await asyncio.sleep(0.1)
                    continue

                # Subscribe to the current job's events
                channel = f"job:{job_id}"
                await pubsub.subscribe(channel)

                response_text = ""

                async for _channel, event_data in pubsub.listen():
                    if session.state == SessionState.ENDED:
                        break

                    event_type = event_data.get("type", "")
                    payload = event_data.get("data", event_data)

                    if event_type == "delta":
                        # Accumulate text for TTS and relay to client
                        chunk = payload.get("content", "")
                        if chunk:
                            response_text += chunk
                            # Forward delta to client
                            redis = await get_redis_client()
                            await redis.publish(
                                f"live_session:{session_id}",
                                json.dumps({
                                    "type": "agent_delta",
                                    "text": chunk,
                                }),
                            )

                    elif event_type in ("complete", "error"):
                        # Job done — synthesize full response via TTS
                        if response_text.strip() and session.state != SessionState.ENDED:
                            session.state = SessionState.SPEAKING
                            await self._publish_status(session_id, "speaking")
                            session.tts_interrupted = False

                            tts_client = self._tts_clients.get(session_id)
                            if tts_client:
                                session.tts_task = asyncio.create_task(
                                    self._stream_tts(
                                        session_id, response_text, tts_client
                                    )
                                )

                        response_text = ""
                        session.current_job_id = None
                        await pubsub.unsubscribe(channel)
                        break

                    elif event_type == "tool_call":
                        # Forward tool calls to client
                        redis = await get_redis_client()
                        await redis.publish(
                            f"live_session:{session_id}",
                            json.dumps({
                                "type": "tool_call",
                                "name": payload.get("name", ""),
                                "args": payload.get("arguments", {}),
                            }),
                        )

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.disconnect()

    async def _stream_tts(
        self,
        session_id: str,
        text: str,
        tts_client: ElevenLabsTTSClient,
    ) -> None:
        """Stream TTS audio to client via Redis."""
        session = self._sessions.get(session_id)
        if not session:
            return

        redis = await get_redis_client()
        seq = 0

        try:
            async for audio_chunk in tts_client.synthesize(text):
                if session.tts_interrupted or session.state == SessionState.ENDED:
                    break

                await redis.publish(
                    f"live_session:{session_id}",
                    json.dumps({
                        "type": "audio_response",
                        "data": audio_chunk,  # base64-encoded PCM
                        "seq": seq,
                    }),
                )
                seq += 1

        except asyncio.CancelledError:
            pass
        finally:
            if not session.tts_interrupted:
                session.state = SessionState.LISTENING
                await self._publish_status(session_id, "listening")

    async def _session_timeout(self, session_id: str, timeout: int) -> None:
        """End session after timeout."""
        try:
            await asyncio.sleep(timeout)
            logger.info("Session timeout reached", session_id=session_id)
            await self.end_session(session_id)
        except asyncio.CancelledError:
            pass

    async def _publish_status(self, session_id: str, state: str) -> None:
        """Publish status update to client."""
        redis = await get_redis_client()
        await redis.publish(
            f"live_session:{session_id}",
            json.dumps({"type": "status", "state": state}),
        )
        # Debug: log state transitions for live session
        try:
            self._write_debug(
                "Session state changed",
                {"session_id": session_id, "state": state},
                "STATE",
            )
        except Exception:
            pass

    async def _persist_session_start(self, session: LiveSessionData) -> None:
        """Persist new session to database."""
        async with get_session_context() as db_session:
            live_session = LiveSession(
                id=uuid.UUID(session.session_id),
                tenant_id=uuid.UUID(session.tenant_id),
                user_id=uuid.UUID(session.user_id) if session.user_id else None,
                conversation_id=(
                    uuid.UUID(session.conversation_id)
                    if session.conversation_id
                    else None
                ),
                language=session.language,
                tts_voice_id=session.tts_voice_id,
                status=LiveSessionStatus.ACTIVE,
            )
            db_session.add(live_session)
            await db_session.flush()

    async def _persist_session_end(self, session: LiveSessionData) -> None:
        """Update session in database with final stats."""
        from sqlalchemy import update as sa_update

        async with get_session_context() as db_session:
            stmt = (
                sa_update(LiveSession)
                .where(LiveSession.id == uuid.UUID(session.session_id))
                .values(
                    status=LiveSessionStatus.ENDED,
                    ended_at=datetime.now(UTC),
                    audio_input_seconds=session.audio_input_seconds,
                    audio_output_seconds=session.audio_output_seconds,
                    screen_frames_count=session.screen_frames_count,
                    total_turns=session.total_turns,
                    conversation_id=(
                        uuid.UUID(session.conversation_id)
                        if session.conversation_id
                        else None
                    ),
                )
            )
            await db_session.execute(stmt)

    async def close_all(self) -> None:
        """End all active sessions."""
        for session_id in list(self._sessions.keys()):
            await self.end_session(session_id)
