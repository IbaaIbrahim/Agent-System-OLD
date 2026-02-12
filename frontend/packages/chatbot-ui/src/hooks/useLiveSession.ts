/**
 * useLiveSession — orchestrates the live assistant session.
 *
 * Connects VAD, audio playback, screen capture, and WebSocket client.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { LiveWebSocketClient, LiveWSMessage } from '../api/LiveWebSocketClient';
import { useAudioPlayback } from './useAudioPlayback';
import { useScreenCapture } from './useScreenCapture';
import { float32ToBase64PCM, useVAD } from './useVAD';

export type LiveState = 'disconnected' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'idle';

export interface LiveTranscript {
    text: string;
    isFinal: boolean;
    timestamp: number;
}

export interface UseLiveSessionOptions {
    wsUrl: string;
    token: string;
    /** Called when a final user transcript is produced. */
    onUserTranscript?: (text: string) => void;
    /** Called when agent text delta arrives. */
    onAgentDelta?: (text: string) => void;
    /** Called when agent response is complete. */
    onAgentComplete?: (fullText: string) => void;
    /** Called on connection error. */
    onError?: (message: string) => void;
}

export interface UseLiveSessionReturn {
    /** Current live assistant state. */
    state: LiveState;
    /** Start the live session. */
    start: (options?: { language?: string; conversation_id?: string }) => Promise<void>;
    /** End the live session. */
    end: () => void;
    /** Pause the session. */
    pause: () => void;
    /** Resume the session. */
    resume: () => void;
    /** Whether screen sharing is active. */
    isScreenSharing: boolean;
    /** Start screen sharing. */
    startScreenShare: () => Promise<void>;
    /** Stop screen sharing. */
    stopScreenShare: () => void;
    /** Capture and send a screen frame. */
    sendScreenFrame: (context?: string) => Promise<void>;
    /** Current transcript (interim). */
    currentTranscript: string;
    /** Whether audio is playing. */
    isAudioPlaying: boolean;
    /** Session ID. */
    sessionId: string | null;
    /** Error message. */
    error: string | null;
}

export function useLiveSession(options: UseLiveSessionOptions): UseLiveSessionReturn {
    const { wsUrl, token, onUserTranscript, onAgentDelta, onAgentComplete, onError } = options;

    const [state, setState] = useState<LiveState>('disconnected');
    const [currentTranscript, setCurrentTranscript] = useState('');
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const wsRef = useRef<LiveWebSocketClient | null>(null);
    const audioSeqRef = useRef(0);
    const agentTextRef = useRef('');
    const callbacksRef = useRef({ onUserTranscript, onAgentDelta, onAgentComplete, onError });
    callbacksRef.current = { onUserTranscript, onAgentDelta, onAgentComplete, onError };

    // Audio playback
    const { isPlaying: isAudioPlaying, enqueue: enqueueAudio, stop: stopAudio, destroy: destroyAudio } = useAudioPlayback();

    // Screen capture
    const { isSharing: isScreenSharing, startSharing, stopSharing, captureFrame } = useScreenCapture();

    // Handle incoming WS messages
    const handleWSMessage = useCallback((msg: LiveWSMessage) => {
        switch (msg.type) {
            case 'session_started':
                setSessionId(msg.session_id);
                setState('listening');
                break;

            case 'session_ended':
                setSessionId(null);
                setState('disconnected');
                break;

            case 'transcript':
                setCurrentTranscript(msg.text || '');
                if (msg.is_final && msg.text?.trim()) {
                    callbacksRef.current.onUserTranscript?.(msg.text);
                    setCurrentTranscript('');
                }
                break;

            case 'audio_response':
                enqueueAudio(msg.data);
                break;

            case 'agent_delta':
                agentTextRef.current += msg.text || '';
                callbacksRef.current.onAgentDelta?.(msg.text || '');
                break;

            case 'status':
                if (msg.state === 'listening') setState('listening');
                else if (msg.state === 'thinking') setState('thinking');
                else if (msg.state === 'speaking') setState('speaking');
                else if (msg.state === 'paused') setState('paused');
                else if (msg.state === 'idle') setState('idle');
                else if (msg.state === 'ended') setState('disconnected');

                // On transition from speaking/thinking back to listening, emit complete
                if (msg.state === 'listening' && agentTextRef.current.trim()) {
                    callbacksRef.current.onAgentComplete?.(agentTextRef.current);
                    agentTextRef.current = '';
                }
                break;

            case 'error':
                setError(msg.message || 'Unknown error');
                callbacksRef.current.onError?.(msg.message || 'Unknown error');
                break;
        }
    }, [enqueueAudio]);

    // VAD callbacks
    const onSpeechStart = useCallback(() => {
        // Interrupt TTS if agent is speaking
        if (isAudioPlaying) {
            stopAudio();
            wsRef.current?.interrupt();
        }
    }, [isAudioPlaying, stopAudio]);

    const onSpeechEnd = useCallback((audio: Float32Array) => {
        if (!wsRef.current?.isConnected) return;

        const base64 = float32ToBase64PCM(audio);
        wsRef.current.sendAudio(base64, audioSeqRef.current++);
    }, []);

    // VAD hook
    const { isListening, isSpeaking, start: startVAD, stop: stopVAD, error: vadError } = useVAD({
        onSpeechStart,
        onSpeechEnd,
    });

    // Start live session
    const start = useCallback(async (sessionOptions?: { language?: string; conversation_id?: string }) => {
        setState('connecting');
        setError(null);
        audioSeqRef.current = 0;
        agentTextRef.current = '';

        // Create WS client
        const ws = new LiveWebSocketClient({
            url: wsUrl,
            token,
            onMessage: handleWSMessage,
            onOpen: () => {
                // Start live session after WS connected
                ws.startSession(sessionOptions);
            },
            onClose: (_code, reason) => {
                setState('disconnected');
                if (reason && reason !== 'Client disconnect') {
                    setError(`Connection closed: ${reason}`);
                }
            },
            onError: () => {
                setError('WebSocket connection error');
            },
        });

        wsRef.current = ws;
        ws.connect();

        // Start VAD
        await startVAD();
    }, [wsUrl, token, handleWSMessage, startVAD]);

    // End live session
    const end = useCallback(() => {
        stopVAD();
        stopAudio();
        stopSharing();

        if (wsRef.current) {
            wsRef.current.endSession();
            wsRef.current.disconnect();
            wsRef.current = null;
        }

        setState('disconnected');
        setSessionId(null);
        setCurrentTranscript('');
        agentTextRef.current = '';
    }, [stopVAD, stopAudio, stopSharing]);

    const pause = useCallback(() => {
        wsRef.current?.pause();
    }, []);

    const resume = useCallback(() => {
        wsRef.current?.resume();
    }, []);

    // Screen sharing
    const startScreenShare = useCallback(async () => {
        await startSharing();
    }, [startSharing]);

    const stopScreenShare = useCallback(() => {
        stopSharing();
    }, [stopSharing]);

    const sendScreenFrame = useCallback(async (context?: string) => {
        const frame = await captureFrame();
        if (frame && wsRef.current?.isConnected) {
            wsRef.current.sendScreenFrame(frame, context);
        }
    }, [captureFrame]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            stopVAD();
            destroyAudio();
            stopSharing();
            if (wsRef.current) {
                wsRef.current.disconnect();
                wsRef.current = null;
            }
        };
    }, [stopVAD, destroyAudio, stopSharing]);

    return {
        state,
        start,
        end,
        pause,
        resume,
        isScreenSharing,
        startScreenShare,
        stopScreenShare,
        sendScreenFrame,
        currentTranscript,
        isAudioPlaying,
        sessionId,
        error: error || vadError,
    };
}
