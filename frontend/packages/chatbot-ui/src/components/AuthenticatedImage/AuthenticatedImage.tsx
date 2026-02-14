import React from 'react';
const { useState, useEffect } = React;
import { useChatbotContext } from '../../context/ChatbotContext';

export interface AuthenticatedImageProps {
    /**
     * The URL to fetch (requires authentication)
     */
    src: string;
    /**
     * Alt text for the image
     */
    alt?: string;
    /**
     * CSS class name
     */
    className?: string;
    /**
     * Click handler
     */
    onClick?: () => void;
    /**
     * Error handler when image fails to load
     */
    onError?: (e: React.SyntheticEvent<HTMLImageElement, Event>) => void;
    /**
     * Style object
     */
    style?: React.CSSProperties;
}

/**
 * Image component that fetches images using authenticated requests.
 * Uses the ChatbotContext to get the authentication credentials.
 * Falls back to direct URL if context is not available.
 */
export const AuthenticatedImage: React.FC<AuthenticatedImageProps> = ({
    src,
    alt = 'Image',
    className,
    onClick,
    onError,
    style,
}) => {
    const context = useChatbotContext();
    const [imageSrc, setImageSrc] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [hasError, setHasError] = useState(false);

    useEffect(() => {
        let isMounted = true;

        const loadImage = async () => {
            setIsLoading(true);
            setHasError(false);

            // If no context, use direct URL (will likely fail for authenticated endpoints)
            if (!context) {
                setImageSrc(src);
                setIsLoading(false);
                return;
            }

            try {
                const authenticatedUrl = await context.fetchAuthenticatedUrl(src);
                if (isMounted) {
                    setImageSrc(authenticatedUrl);
                    setIsLoading(false);
                }
            } catch (error) {
                console.error('Failed to load authenticated image:', error);
                if (isMounted) {
                    setHasError(true);
                    setIsLoading(false);
                }
            }
        };

        loadImage();

        return () => {
            isMounted = false;
        };
    }, [src, context]);

    const handleError = (e: React.SyntheticEvent<HTMLImageElement, Event>) => {
        setHasError(true);
        onError?.(e);
    };

    if (isLoading) {
        return (
            <div
                className={className}
                style={{
                    ...style,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minHeight: 60,
                    background: 'rgba(255, 255, 255, 0.04)',
                }}
            >
                <span style={{ fontSize: 11, color: 'rgba(255, 255, 255, 0.5)' }}>
                    Loading...
                </span>
            </div>
        );
    }

    if (hasError || !imageSrc) {
        return (
            <div
                className={className}
                style={{
                    ...style,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minHeight: 60,
                    background: 'rgba(255, 255, 255, 0.04)',
                }}
            >
                <span style={{ fontSize: 11, color: 'rgba(255, 255, 255, 0.5)' }}>
                    Image unavailable
                </span>
            </div>
        );
    }

    return (
        <img
            src={imageSrc}
            alt={alt}
            className={className}
            onClick={onClick}
            onError={handleError}
            style={style}
        />
    );
};
