/**
 * useVAD — Voice Activity Detection hook using @ricky0123/vad-web.
 *
 * Uses Silero VAD model via ONNX Runtime Web, runs in a Web Worker.
 * Detects speech start/end for natural turn-taking.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseVADOptions {
    /** Called when speech is detected to start. */
    onSpeechStart?: () => void;
    /** Called when speech ends, with the audio data as Float32Array. */
    onSpeechEnd?: (audio: Float32Array) => void;
    /** Positive speech threshold (0-1). Higher = more strict. */
    positiveSpeechThreshold?: number;
    /** Negative speech threshold (0-1). Lower = more strict. */
    negativeSpeechThreshold?: number;
    /** Min speech duration in ms before triggering. */
    minSpeechMs?: number;
    /** Pre-speech padding in ms. */
    preSpeechPadMs?: number;
}

export interface UseVADReturn {
    /** Whether VAD is currently listening. */
    isListening: boolean;
    /** Whether speech is currently detected. */
    isSpeaking: boolean;
    /** Start the VAD listener. */
    start: () => Promise<void>;
    /** Stop the VAD listener. */
    stop: () => void;
    /** Error if VAD failed to initialize. */
    error: string | null;
}

export function useVAD(options: UseVADOptions = {}): UseVADReturn {
    const {
        onSpeechStart,
        onSpeechEnd,
        positiveSpeechThreshold = 0.8,
        negativeSpeechThreshold = 0.15,
        minSpeechMs = 150,
        preSpeechPadMs = 90,
    } = options;

    const [isListening, setIsListening] = useState(false);
    const [isSpeaking, setIsSpeaking] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const vadRef = useRef<any>(null);
    const callbacksRef = useRef({ onSpeechStart, onSpeechEnd });
    callbacksRef.current = { onSpeechStart, onSpeechEnd };

    const start = useCallback(async () => {
        if (vadRef.current) return;

        try {
            // Dynamic import to avoid bundle bloat
            const { MicVAD } = await import('@ricky0123/vad-web');

            vadRef.current = await MicVAD.new({
                // Load ONNX Runtime WASM + Silero model from CDN to avoid
                // needing to copy files into each host app's public directory
                onnxWASMBasePath: 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/',
                baseAssetPath: 'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/dist/',
                model: 'legacy',
                positiveSpeechThreshold,
                negativeSpeechThreshold,
                minSpeechMs,
                preSpeechPadMs,
                onSpeechStart: () => {
                    setIsSpeaking(true);
                    callbacksRef.current.onSpeechStart?.();
                },
                onSpeechEnd: (audio: Float32Array) => {
                    setIsSpeaking(false);
                    callbacksRef.current.onSpeechEnd?.(audio);
                },
            });

            vadRef.current.start();
            setIsListening(true);
            setError(null);
        } catch (e: any) {
            console.error('[VAD] Failed to initialize:', e);
            setError(e.message || 'Failed to initialize VAD');
        }
    }, [positiveSpeechThreshold, negativeSpeechThreshold, minSpeechMs, preSpeechPadMs]);

    const stop = useCallback(() => {
        if (vadRef.current) {
            vadRef.current.pause();
            vadRef.current.destroy();
            vadRef.current = null;
        }
        setIsListening(false);
        setIsSpeaking(false);
    }, []);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (vadRef.current) {
                vadRef.current.pause();
                vadRef.current.destroy();
                vadRef.current = null;
            }
        };
    }, []);

    return { isListening, isSpeaking, start, stop, error };
}

/**
 * Convert Float32Array audio to base64-encoded PCM 16-bit at 16kHz.
 * VAD outputs Float32Array at 16kHz sample rate.
 */
export function float32ToBase64PCM(float32Audio: Float32Array): string {
    // Convert float32 (-1 to 1) to int16 (-32768 to 32767)
    const int16 = new Int16Array(float32Audio.length);
    for (let i = 0; i < float32Audio.length; i++) {
        const s = Math.max(-1, Math.min(1, float32Audio[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }

    // Convert to base64
    const bytes = new Uint8Array(int16.buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}
