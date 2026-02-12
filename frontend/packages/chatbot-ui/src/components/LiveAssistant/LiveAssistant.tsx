/**
 * LiveAssistant — main container for real-time voice + vision interaction.
 *
 * Renders voice visualizer, status bar, transcript, and control buttons.
 * Wires up useLiveSession hook for all real-time functionality.
 */

import React, { useCallback, useEffect } from 'react';

import { useLiveSession, LiveState } from '../../hooks/useLiveSession';
import './LiveAssistant.css';

export interface LiveAssistantProps {
    /** WebSocket gateway URL (e.g., ws://localhost:8002/ws). */
    wsUrl: string;
    /** Auth token for WebSocket connection. */
    token: string;
    /** Current conversation ID to continue in live mode. */
    conversationId?: string;
    /** Called when user speech is transcribed. */
    onUserMessage?: (text: string) => void;
    /** Called when agent produces text. */
    onAgentDelta?: (text: string) => void;
    /** Called when agent response is complete. */
    onAgentComplete?: (text: string) => void;
    /** Called when session ends. */
    onEnd?: () => void;
}

const STATE_LABELS: Record<LiveState, string> = {
    disconnected: 'Disconnected',
    connecting: 'Connecting...',
    listening: 'Listening',
    thinking: 'Thinking...',
    speaking: 'Speaking',
    paused: 'Paused',
    idle: 'Ready',
};

export const LiveAssistant: React.FC<LiveAssistantProps> = ({
    wsUrl,
    token,
    conversationId,
    onUserMessage,
    onAgentDelta,
    onAgentComplete,
    onEnd,
}) => {
    const {
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
        error,
    } = useLiveSession({
        wsUrl,
        token,
        onUserTranscript: onUserMessage,
        onAgentDelta,
        onAgentComplete,
    });

    // Auto-start session when component mounts
    useEffect(() => {
        start({ conversation_id: conversationId });
        return () => {
            // Don't auto-end — let user control
        };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const handleEnd = useCallback(() => {
        end();
        onEnd?.();
    }, [end, onEnd]);

    const handleScreenToggle = useCallback(async () => {
        if (isScreenSharing) {
            stopScreenShare();
        } else {
            await startScreenShare();
        }
    }, [isScreenSharing, startScreenShare, stopScreenShare]);

    const handleSendFrame = useCallback(async () => {
        await sendScreenFrame('Describe what you see on the screen in detail.');
    }, [sendScreenFrame]);

    const isActive = state !== 'disconnected';

    return (
        <div className="cb-live-assistant">
            {/* Status indicator */}
            <div className={`cb-live-status cb-live-status--${state}`}>
                <span className="cb-live-status-dot" />
                {STATE_LABELS[state]}
            </div>

            {/* Voice visualizer */}
            <VoiceVisualizer
                isActive={state === 'listening' || state === 'speaking'}
                isSpeaking={state === 'speaking' || isAudioPlaying}
            />

            {/* Interim transcript */}
            {currentTranscript && (
                <div className="cb-live-transcript">{currentTranscript}</div>
            )}

            {/* Error */}
            {error && <div className="cb-live-error">{error}</div>}

            {/* Controls */}
            {isActive && (
                <div className="cb-live-controls">
                    {/* Mute / Pause */}
                    <button
                        className="cb-live-btn"
                        onClick={state === 'paused' ? resume : pause}
                        title={state === 'paused' ? 'Resume' : 'Mute'}
                    >
                        {state === 'paused' ? '\u25B6' : '\u23F8'}
                    </button>

                    {/* Screen share */}
                    <button
                        className={`cb-live-btn cb-live-btn--screen ${isScreenSharing ? 'cb-live-btn--active' : ''}`}
                        onClick={handleScreenToggle}
                        title={isScreenSharing ? 'Stop sharing' : 'Share screen'}
                    >
                        {'\uD83D\uDCBB'}
                    </button>

                    {/* Send frame (only when sharing) */}
                    {isScreenSharing && (
                        <button
                            className="cb-live-btn"
                            onClick={handleSendFrame}
                            title="Send screen to agent"
                        >
                            {'\uD83D\uDC41'}
                        </button>
                    )}

                    {/* End call */}
                    <button
                        className="cb-live-btn cb-live-btn--end"
                        onClick={handleEnd}
                        title="End session"
                    >
                        {'\u260E'}
                    </button>
                </div>
            )}
        </div>
    );
};

/** Voice level visualizer bars. */
const VoiceVisualizer: React.FC<{ isActive: boolean; isSpeaking: boolean }> = ({
    isActive,
    isSpeaking,
}) => {
    const barCount = 5;

    return (
        <div className="cb-voice-visualizer">
            {Array.from({ length: barCount }).map((_, i) => (
                <div
                    key={i}
                    className={`cb-voice-bar ${isActive || isSpeaking ? 'cb-voice-bar--active' : ''}`}
                    style={{
                        animationDelay: `${i * 0.08}s`,
                    }}
                />
            ))}
        </div>
    );
};


/** Toggle button to switch between text and live mode. */
export const LiveModeToggle: React.FC<{
    isLive: boolean;
    onToggle: () => void;
}> = ({ isLive, onToggle }) => {
    return (
        <button
            className={`cb-live-toggle ${isLive ? 'cb-live-toggle--active' : ''}`}
            onClick={onToggle}
            title={isLive ? 'Switch to text mode' : 'Switch to live mode'}
        >
            {'\uD83C\uDF99'} {isLive ? 'Live' : 'Voice'}
        </button>
    );
};
