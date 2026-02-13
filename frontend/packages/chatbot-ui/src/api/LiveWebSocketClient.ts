/**
 * LiveWebSocketClient — manages WebSocket connection to the Live Assistant gateway.
 *
 * Handles auth, reconnection, and message routing.
 */

export type LiveWSMessageType =
    | 'connected'
    | 'session_started'
    | 'session_ended'
    | 'transcript'
    | 'audio_response'
    | 'agent_delta'
    | 'tool_call'
    | 'status'
    | 'frame_captured'
    | 'error'
    | 'pong';

export interface LiveWSMessage {
    type: LiveWSMessageType;
    [key: string]: any;
}

export type LiveWSListener = (message: LiveWSMessage) => void;

export interface LiveWSConfig {
    url: string;         // ws://localhost:8002/ws
    token: string;       // Auth token (Bearer sk-agent-... or JWT)
    onMessage: LiveWSListener;
    onOpen?: () => void;
    onClose?: (code: number, reason: string) => void;
    onError?: (error: Event) => void;
    reconnect?: boolean;
    reconnectInterval?: number;  // ms
    maxReconnectAttempts?: number;
}

export class LiveWebSocketClient {
    private ws: WebSocket | null = null;
    private config: LiveWSConfig;
    private reconnectAttempts = 0;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private authenticated = false;
    private sessionId: string | null = null;
    private pingInterval: ReturnType<typeof setInterval> | null = null;

    constructor(config: LiveWSConfig) {
        this.config = {
            reconnect: true,
            reconnectInterval: 3000,
            maxReconnectAttempts: 5,
            ...config,
        };
    }

    connect(): void {
        if (this.ws?.readyState === WebSocket.OPEN) return;

        this.ws = new WebSocket(this.config.url);
        this.ws.onopen = () => this.handleOpen();
        this.ws.onmessage = (event) => this.handleMessage(event);
        this.ws.onclose = (event) => this.handleClose(event);
        this.ws.onerror = (event) => this.config.onError?.(event);
    }

    disconnect(): void {
        this.stopPing();
        this.clearReconnect();
        this.authenticated = false;
        this.sessionId = null;

        if (this.ws) {
            this.ws.onclose = null; // Prevent reconnect
            this.ws.close(1000, 'Client disconnect');
            this.ws = null;
        }
    }

    get isConnected(): boolean {
        return this.ws?.readyState === WebSocket.OPEN && this.authenticated;
    }

    get currentSessionId(): string | null {
        return this.sessionId;
    }

    /** Start a live session. */
    startSession(options?: {
        language?: string;
        tts_voice_id?: string;
        conversation_id?: string;
    }): void {
        this.send({
            type: 'start_session',
            ...options,
        });
    }

    /** End the current live session. */
    endSession(): void {
        this.send({ type: 'control', action: 'end' });
        this.sessionId = null;
    }

    /** Send an audio chunk (base64 PCM 16kHz). */
    sendAudio(data: string, seq: number): void {
        this.send({
            type: 'audio',
            data,
            seq,
            sample_rate: 16000,
        });
    }

    /** Send a screen frame (base64 PNG). */
    sendScreenFrame(data: string, context?: string): void {
        this.send({
            type: 'screen_frame',
            data,
            context: context || 'Describe what is visible on the screen.',
            timestamp: new Date().toISOString(),
        });
    }

    /** Interrupt current TTS output. */
    interrupt(): void {
        this.send({ type: 'control', action: 'interrupt' });
    }

    /** Pause the live session. */
    pause(): void {
        this.send({ type: 'control', action: 'pause' });
    }

    /** Resume the live session. */
    resume(): void {
        this.send({ type: 'control', action: 'resume' });
    }

    private send(data: object): void {
        if (this.ws?.readyState !== WebSocket.OPEN) {
            console.warn('[LiveWS] Cannot send — not connected');
            return;
        }
        this.ws.send(JSON.stringify(data));
    }

    private handleOpen(): void {
        console.log('[LiveWS] WebSocket opened');
        this.reconnectAttempts = 0;

        // Send auth message immediately
        this.send({
            type: 'auth',
            token: this.config.token,
        });
    }

    private handleMessage(event: MessageEvent): void {
        let message: LiveWSMessage;
        try {
            message = JSON.parse(event.data);
        } catch {
            console.warn('[LiveWS] Non-JSON message:', event.data);
            return;
        }

        // Handle auth success
        if (message.type === 'connected') {
            this.authenticated = true;
            this.startPing();
            this.config.onOpen?.();
        }

        // Track session ID
        if (message.type === 'session_started') {
            this.sessionId = message.session_id;
        }

        if (message.type === 'session_ended') {
            this.sessionId = null;
        }

        // Forward all messages to listener
        this.config.onMessage(message);
    }

    private handleClose(event: CloseEvent): void {
        this.stopPing();
        this.authenticated = false;
        this.config.onClose?.(event.code, event.reason);

        // Reconnect on unexpected close
        if (
            this.config.reconnect &&
            event.code !== 1000 &&
            this.reconnectAttempts < (this.config.maxReconnectAttempts ?? 5)
        ) {
            this.reconnectAttempts++;
            const delay = this.config.reconnectInterval! * this.reconnectAttempts;
            console.log(`[LiveWS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
            this.reconnectTimer = setTimeout(() => this.connect(), delay);
        }
    }

    private startPing(): void {
        this.pingInterval = setInterval(() => {
            this.send({ type: 'ping' });
        }, 20000);
    }

    private stopPing(): void {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    private clearReconnect(): void {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        this.reconnectAttempts = 0;
    }
}
