import React from 'react';
const { createContext, useContext } = React;

export interface ChatbotContextValue {
    /**
     * Function to fetch authenticated resources.
     * Returns a blob URL that can be used in img src, etc.
     */
    fetchAuthenticatedUrl: (url: string) => Promise<string>;
}

const ChatbotContext = createContext<ChatbotContextValue | null>(null);

export interface ChatbotProviderProps {
    children: React.ReactNode;
    /**
     * Access token for API authentication
     */
    accessToken?: string | null;
    /**
     * Base URL for API requests
     */
    apiBaseUrl?: string;
}

/**
 * Provider component that enables authenticated resource fetching
 * for images and files displayed in the chat.
 */
export const ChatbotProvider: React.FC<ChatbotProviderProps> = ({
    children,
    accessToken,
    apiBaseUrl = 'http://localhost:8000/api',
}) => {
    // Cache for blob URLs to avoid refetching
    const blobCacheRef = React.useRef<Map<string, string>>(new Map());

    const fetchAuthenticatedUrl = React.useCallback(async (url: string): Promise<string> => {
        // Check cache first
        const cached = blobCacheRef.current.get(url);
        if (cached) {
            return cached;
        }

        // If no token, throw so AuthenticatedImage shows fallback
        if (!accessToken) {
            throw new Error('No access token available for authenticated fetch');
        }

        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
        });

        if (!response.ok) {
            throw new Error(`Failed to fetch authenticated URL: ${response.status}`);
        }

        const blob = await response.blob();
        const blobUrl = URL.createObjectURL(blob);

        // Cache the blob URL
        blobCacheRef.current.set(url, blobUrl);

        return blobUrl;
    }, [accessToken]);

    // Cleanup blob URLs on unmount
    React.useEffect(() => {
        const cache = blobCacheRef.current;
        return () => {
            cache.forEach((blobUrl) => {
                URL.revokeObjectURL(blobUrl);
            });
            cache.clear();
        };
    }, []);

    const value = React.useMemo(() => ({
        fetchAuthenticatedUrl,
    }), [fetchAuthenticatedUrl]);

    return (
        <ChatbotContext.Provider value={value}>
            {children}
        </ChatbotContext.Provider>
    );
};

/**
 * Hook to access the chatbot context for authenticated resource fetching
 */
export const useChatbotContext = (): ChatbotContextValue | null => {
    return useContext(ChatbotContext);
};
