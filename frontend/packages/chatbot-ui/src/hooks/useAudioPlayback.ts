/**
 * useAudioPlayback — streaming PCM audio playback via AudioContext.
 *
 * Receives base64-encoded PCM chunks and plays them with gapless buffering.
 * Supports interruption (stop all queued audio immediately).
 */

import { useCallback, useRef, useState } from 'react';

export interface UseAudioPlaybackReturn {
    /** Whether audio is currently playing. */
    isPlaying: boolean;
    /** Enqueue a base64-encoded PCM audio chunk for playback. */
    enqueue: (audioBase64: string) => void;
    /** Stop all playback immediately (for interruption). */
    stop: () => void;
    /** Clean up resources. */
    destroy: () => void;
}

const SAMPLE_RATE = 24000; // ElevenLabs PCM output rate

export function useAudioPlayback(): UseAudioPlaybackReturn {
    const [isPlaying, setIsPlaying] = useState(false);

    const contextRef = useRef<AudioContext | null>(null);
    const queueRef = useRef<AudioBufferSourceNode[]>([]);
    const nextTimeRef = useRef(0);

    const getContext = useCallback(() => {
        if (!contextRef.current || contextRef.current.state === 'closed') {
            contextRef.current = new AudioContext({ sampleRate: SAMPLE_RATE });
        }
        return contextRef.current;
    }, []);

    const enqueue = useCallback((audioBase64: string) => {
        const ctx = getContext();

        // Ensure context is running (browser autoplay policy)
        if (ctx.state === 'suspended') {
            ctx.resume();
        }

        // Decode base64 to raw PCM bytes
        const binaryStr = atob(audioBase64);
        const bytes = new Uint8Array(binaryStr.length);
        for (let i = 0; i < binaryStr.length; i++) {
            bytes[i] = binaryStr.charCodeAt(i);
        }

        // Convert PCM 16-bit to Float32
        const int16 = new Int16Array(bytes.buffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / 32768;
        }

        // Create audio buffer
        const buffer = ctx.createBuffer(1, float32.length, SAMPLE_RATE);
        buffer.copyToChannel(float32, 0);

        // Schedule playback
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);

        const now = ctx.currentTime;
        const startTime = Math.max(now, nextTimeRef.current);
        source.start(startTime);
        nextTimeRef.current = startTime + buffer.duration;

        source.onended = () => {
            const idx = queueRef.current.indexOf(source);
            if (idx !== -1) queueRef.current.splice(idx, 1);
            if (queueRef.current.length === 0) {
                setIsPlaying(false);
            }
        };

        queueRef.current.push(source);
        setIsPlaying(true);
    }, [getContext]);

    const stop = useCallback(() => {
        // Stop all queued sources immediately
        for (const source of queueRef.current) {
            try {
                source.stop();
            } catch {
                // Already stopped
            }
        }
        queueRef.current = [];
        nextTimeRef.current = 0;
        setIsPlaying(false);
    }, []);

    const destroy = useCallback(() => {
        stop();
        if (contextRef.current) {
            contextRef.current.close();
            contextRef.current = null;
        }
    }, [stop]);

    return { isPlaying, enqueue, stop, destroy };
}
