import React from 'react';
const { createContext, useContext } = React;

export interface ChatbotContextValue {
    /**
     * Function to fetch authenticated resources.
     * Returns a blob URL that can be used in img src, etc.
     */
    fetchAuthenticatedUrl: (url: string) => Promise<string>;
    /**
     * Get an openable download URL for a file (with one-time token).
     * Use this for file attachments so the link works in a new tab.
     */
    getFileDownloadUrl: (fileIdOrDownloadUrl: string) => Promise<string>;
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
        // Resolve relative URLs against apiBaseUrl so fetch goes to the API server
        const resolvedUrl = url.startsWith('http') ? url : `${apiBaseUrl.replace(/\/$/, '')}${url.startsWith('/') ? url : `/${url}`}`;

        // Check cache first (use resolved URL as key)
        const cached = blobCacheRef.current.get(resolvedUrl);
        if (cached) {
            return cached;
        }

        // If no token, throw so AuthenticatedImage shows fallback
        if (!accessToken) {
            throw new Error('No access token available for authenticated fetch');
        }

        const response = await fetch(resolvedUrl, {
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
        blobCacheRef.current.set(resolvedUrl, blobUrl);

        return blobUrl;
    }, [accessToken, apiBaseUrl]);

    const getFileDownloadUrl = React.useCallback(async (fileIdOrDownloadUrl: string): Promise<string> => {
        if (!accessToken) {
            throw new Error('No access token available');
        }
        const base = apiBaseUrl.replace(/\/$/, '');
        const uuidLike = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
        const fileId = uuidLike.test(fileIdOrDownloadUrl)
            ? fileIdOrDownloadUrl
            : (fileIdOrDownloadUrl.match(/\/files\/([a-f0-9-]+)(?:\/download)?\/?$/i)
                || fileIdOrDownloadUrl.match(/\/v1\/files\/([a-f0-9-]+)(?:\/download)?\/?$/i))?.[1] ?? fileIdOrDownloadUrl;
        const url = `${base}/v1/files/${fileId}/download-url`;
        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
        });
        if (!response.ok) {
            throw new Error(`Failed to get download URL: ${response.status}`);
        }
        const data = await response.json();
        return data.url as string;
    }, [accessToken, apiBaseUrl]);

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
        getFileDownloadUrl,
    }), [fetchAuthenticatedUrl, getFileDownloadUrl]);

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
