/**
 * useScreenCapture — on-demand screen capture via getDisplayMedia.
 *
 * Captures the user's screen/window/tab and extracts frames as base64 PNG.
 */

import { useCallback, useRef, useState } from 'react';

export interface UseScreenCaptureReturn {
    /** Whether screen sharing is active. */
    isSharing: boolean;
    /** Start screen sharing (prompts user for permission). */
    startSharing: () => Promise<void>;
    /** Stop screen sharing. */
    stopSharing: () => void;
    /** Capture a single frame as base64 PNG. */
    captureFrame: () => Promise<string | null>;
    /** Error if screen capture failed. */
    error: string | null;
}

export function useScreenCapture(): UseScreenCaptureReturn {
    const [isSharing, setIsSharing] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const streamRef = useRef<MediaStream | null>(null);
    const videoRef = useRef<HTMLVideoElement | null>(null);

    const startSharing = useCallback(async () => {
        try {
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                    frameRate: { ideal: 2 },
                },
            });

            streamRef.current = stream;

            // Create hidden video element to capture frames from
            const video = document.createElement('video');
            video.srcObject = stream;
            video.muted = true;
            await video.play();
            videoRef.current = video;

            // Handle user stopping share via browser UI
            stream.getVideoTracks()[0].onended = () => {
                stopSharing();
            };

            setIsSharing(true);
            setError(null);
        } catch (e: any) {
            if (e.name === 'NotAllowedError') {
                setError('Screen sharing permission denied');
            } else {
                setError(e.message || 'Failed to start screen sharing');
            }
        }
    }, []);

    const stopSharing = useCallback(() => {
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(track => track.stop());
            streamRef.current = null;
        }
        if (videoRef.current) {
            videoRef.current.srcObject = null;
            videoRef.current = null;
        }
        setIsSharing(false);
    }, []);

    const captureFrame = useCallback(async (): Promise<string | null> => {
        const video = videoRef.current;
        if (!video || !isSharing) return null;

        try {
            // Draw current video frame to a canvas
            const canvas = document.createElement('canvas');
            // Downscale to 720x512 max
            const maxWidth = 720;
            const maxHeight = 512;
            const ratio = Math.min(maxWidth / video.videoWidth, maxHeight / video.videoHeight);

            canvas.width = Math.round(video.videoWidth * ratio);
            canvas.height = Math.round(video.videoHeight * ratio);

            const ctx = canvas.getContext('2d');
            if (!ctx) return null;

            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

            // Convert to base64 PNG
            const dataUrl = canvas.toDataURL('image/png');
            return dataUrl.split(',')[1]; // Remove "data:image/png;base64," prefix

        } catch (e: any) {
            console.error('[ScreenCapture] Frame capture failed:', e);
            return null;
        }
    }, [isSharing]);

    return { isSharing, startSharing, stopSharing, captureFrame, error };
}
