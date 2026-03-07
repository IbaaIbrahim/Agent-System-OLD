import React from 'react';
import { ChatContainer, ChatMode } from '../ChatContainer/ChatContainer';
import { MessageBubble, MessageProps } from '../MessageBubble/MessageBubble';
import { Composer, ReplyingTo, ComposerHandle } from '../Composer/Composer';
import { NavigationSidebar, SidebarItem } from '../NavigationSidebar/NavigationSidebar';
import { WelcomeScreen } from '../WelcomeScreen/WelcomeScreen';
import { PendingMessageList } from '../PendingMessageList/PendingMessageList';
import { ThinkingIndicator } from '../ThinkingIndicator/ThinkingIndicator';
import { LiveAssistant, LiveModeToggle } from '../LiveAssistant/LiveAssistant';
import { ChatbotProvider } from '../../context/ChatbotContext';
import { useChat } from '../../hooks/useChat';
import { ChatClient, ConversationSummary } from '../../api/types';

export interface ChatbotProps {
    client: ChatClient | null;
    mode?: ChatMode;
    isOpen?: boolean;
    onClose?: () => void;
    onOpen?: () => void;
    embedded?: boolean;
    userName?: string;
    quickActions?: { id: string; label: string; icon: string; onClick: () => void }[];
    agents?: SidebarItem[];
    chatHistory?: SidebarItem[];
    onToolCall?: Record<string, (args: any) => Promise<any>>;
    headerActions?: React.ReactNode;

    // Library toggles - can be managed internally by Chatbot component
    webSearchEnabled?: boolean;
    onWebSearchChange?: (enabled: boolean) => void;
    pageContextEnabled?: boolean;
    onPageContextChange?: (enabled: boolean) => void;

    // Live assistant mode
    liveAssistantEnabled?: boolean;
    wsUrl?: string;   // WebSocket gateway URL (e.g., ws://localhost:8002/ws)
    wsToken?: string;  // Auth token for WS connection

    // Authentication for file/image display
    accessToken?: string | null;
    apiBaseUrl?: string;
}

export const Chatbot: React.FC<ChatbotProps> = ({
    client,
    mode = 'floating',
    isOpen = true,
    onClose,
    onOpen,
    embedded = false,
    userName = 'User',
    quickActions = [],
    agents = [],
    chatHistory: externalChatHistory,
    onToolCall,
    headerActions,
    webSearchEnabled: controlledWebSearch,
    onWebSearchChange: controlledOnWebSearch,
    pageContextEnabled: controlledPageContext,
    onPageContextChange: controlledOnPageContext,
    liveAssistantEnabled = false,
    wsUrl,
    wsToken,
    accessToken,
    apiBaseUrl,
}) => {
    const [isDrawerOpen, setIsDrawerOpen] = React.useState(false);
    const [isReadingPage, setIsReadingPage] = React.useState(false);
    const [isLiveMode, setIsLiveMode] = React.useState(false);
    const clientRef = React.useRef(client);
    clientRef.current = client;
    const composerRef = React.useRef<ComposerHandle>(null);

    // Conversation state
    const [conversations, setConversations] = React.useState<ConversationSummary[]>([]);
    const [searchResults, setSearchResults] = React.useState<SidebarItem[]>([]);
    const [isSearching, setIsSearching] = React.useState(false);
    const searchTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

    // Internal state for toggles if not controlled
    const [internalWebSearch, setInternalWebSearch] = React.useState(false);
    const [internalPageContext, setInternalPageContext] = React.useState(false);
    const [internalEffortLevel, setInternalEffortLevel] = React.useState<'low' | 'medium' | 'high'>('medium');

    const webSearchEnabled = controlledWebSearch !== undefined ? controlledWebSearch : internalWebSearch;
    const pageContextEnabled = controlledPageContext !== undefined ? controlledPageContext : internalPageContext;

    // Sync initial and subsequent toggle state to client
    React.useEffect(() => {
        if (client) {
            client.enableWebSearch(webSearchEnabled);
            client.enablePageContext(pageContextEnabled);

            // Register page reading callback
            client.setPageReadingCallback?.((isReading: boolean) => {
                setIsReadingPage(isReading);
            });
        }
    }, [client, webSearchEnabled, pageContextEnabled]);

    const setWebSearch = (enabled: boolean) => {
        if (controlledOnWebSearch) {
            controlledOnWebSearch(enabled);
        } else {
            setInternalWebSearch(enabled);
        }
        client?.enableWebSearch(enabled);
    };

    const setPageContext = (enabled: boolean) => {
        if (controlledOnPageContext) {
            controlledOnPageContext(enabled);
        } else {
            setInternalPageContext(enabled);
        }
        client?.enablePageContext(enabled);
    };

    // Sync effort level to client
    React.useEffect(() => {
        client?.setEffortLevel?.(internalEffortLevel);
    }, [client, internalEffortLevel]);

    const setEffortLevel = (level: 'low' | 'medium' | 'high') => {
        setInternalEffortLevel(level);
        client?.setEffortLevel?.(level);
    };

    const {
        messages,
        isThinking,
        isWaitingForDeltas,
        conversationTitle,
        messageQueue,
        sendMessage,
        removeQueueItem,
        handleAnimationComplete,
        finishedMessageIds,
        loadConversation,
        conversationId,
        reset
    } = useChat({ client, onToolCall });

    // Reply state
    const [replyingTo, setReplyingTo] = React.useState<ReplyingTo | null>(null);

    const handleReply = React.useCallback((message: MessageProps, selectedText?: string) => {
        const content = message.content
            || (message.steps || []).filter(s => s.type === 'text').map(s => s.content || '').join('\n\n')
            || '';
        setReplyingTo({
            id: message.id,
            role: message.role,
            content,
            selectedText,
        });
        requestAnimationFrame(() => composerRef.current?.focus());
    }, []);

    const handleEdit = React.useCallback(async (messageId: string, content: string) => {
        const c = clientRef.current;
        if (!c?.editMessage) return;
        try {
            await c.editMessage(messageId, content);
        } catch (err) {
            console.error('Failed to edit message:', err);
        }
    }, []);

    const handleSwitchBranch = React.useCallback(async (branchPointMessageId: string, targetChildMessageId: string) => {
        const c = clientRef.current;
        if (!c?.switchBranch) return;
        try {
            await c.switchBranch(branchPointMessageId, targetChildMessageId);
            // Reload the conversation to get the switched branch
            const convId = c.getConversationId?.();
            if (convId) {
                await loadConversation(convId);
            }
        } catch (err) {
            console.error('Failed to switch branch:', err);
        }
    }, [loadConversation]);

    const handleConfirm = (toolCallId: string) => {
        client?.sendConfirmResponse?.(toolCallId, true);
    };

    const handleReject = (toolCallId: string) => {
        client?.sendConfirmResponse?.(toolCallId, false);
    };

    // Fetch conversations when drawer opens
    const fetchConversations = React.useCallback(async () => {
        const c = clientRef.current;
        if (!c?.getConversations) return;
        try {
            const result = await c.getConversations();
            setConversations(result.conversations);
        } catch (err) {
            console.error('Failed to fetch conversations:', err);
        }
    }, []);

    React.useEffect(() => {
        if (isDrawerOpen) {
            fetchConversations();
        }
    }, [isDrawerOpen, fetchConversations]);

    // Also refresh conversations after a message completes
    React.useEffect(() => {
        if (!isThinking && messages.length > 0 && isDrawerOpen) {
            fetchConversations();
        }
    }, [isThinking, messages.length, isDrawerOpen, fetchConversations]);

    // Update conversation title when auto-generated title arrives via SSE
    React.useEffect(() => {
        if (conversationTitle && conversationId) {
            setConversations(prev =>
                prev.map(c =>
                    c.id === conversationId ? { ...c, title: conversationTitle } : c
                )
            );
        }
    }, [conversationTitle, conversationId]);

    // Delayed refetch after completion to pick up auto-generated titles
    React.useEffect(() => {
        if (!isThinking && messages.length > 0) {
            const timer = setTimeout(() => {
                fetchConversations();
            }, 3000);
            return () => clearTimeout(timer);
        }
    }, [isThinking, messages.length, fetchConversations]);

    const handleLoadConversation = React.useCallback(async (convId: string) => {
        await loadConversation(convId);
        setIsDrawerOpen(false);
        requestAnimationFrame(() => composerRef.current?.focus());
    }, [loadConversation]);

    const startNewChat = () => {
        reset();
        setIsDrawerOpen(false);
        requestAnimationFrame(() => composerRef.current?.focus());
    };

    // Debounced search
    const handleSearch = React.useCallback((query: string) => {
        if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
        }

        if (!query.trim()) {
            setSearchResults([]);
            setIsSearching(false);
            return;
        }

        setIsSearching(true);
        searchTimeoutRef.current = setTimeout(async () => {
            const c = clientRef.current;
            if (!c?.searchConversations) {
                setIsSearching(false);
                return;
            }
            try {
                const result = await c.searchConversations(query);
                setSearchResults(
                    result.conversations.map(conv => ({
                        id: conv.id,
                        label: conv.title,
                        active: conv.id === conversationId,
                        onClick: () => handleLoadConversation(conv.id),
                    }))
                );
            } catch (err) {
                console.error('Search failed:', err);
                setSearchResults([]);
            } finally {
                setIsSearching(false);
            }
        }, 300);
    }, [conversationId, handleLoadConversation]);

    const handleFileUpload = React.useCallback(async (file: File) => {
        const c = clientRef.current;
        if (!c) {
            throw new Error('Chat client not initialized');
        }
        const uploadFn = (c as any).uploadFile;
        if (typeof uploadFn !== 'function') {
            throw new Error('File upload not supported by this client');
        }
        return uploadFn.call(c, file);
    }, []);

    // Build chat history items from fetched conversations (or fallback to external prop)
    const chatHistoryItems: SidebarItem[] = React.useMemo(() => {
        if (conversations.length > 0) {
            return conversations.map(conv => ({
                id: conv.id,
                label: conv.title,
                active: conv.id === conversationId,
                onClick: () => handleLoadConversation(conv.id),
            }));
        }
        return externalChatHistory || [];
    }, [conversations, conversationId, handleLoadConversation, externalChatHistory]);

    const navContent = (
        <NavigationSidebar
            onNewChat={startNewChat}
            agents={agents}
            chatHistory={chatHistoryItems}
            onSearch={client?.searchConversations ? handleSearch : undefined}
            searchResults={searchResults}
            isSearching={isSearching}
        />
    );

    return (
        <ChatbotProvider accessToken={accessToken} apiBaseUrl={apiBaseUrl}>
            {isReadingPage && <div className="cb-page-reading-indicator" />}
            <ChatContainer
                mode={mode}
                isOpen={isOpen}
                embedded={embedded}
                onClose={onClose}
                onOpen={onOpen}
                isDrawerOpen={isDrawerOpen}
                onDrawerOpenChange={setIsDrawerOpen}
                drawerContent={navContent}
                headerActions={
                    <>
                        {liveAssistantEnabled && wsUrl && wsToken && (
                            <LiveModeToggle
                                isLive={isLiveMode}
                                onToggle={() => setIsLiveMode(!isLiveMode)}
                            />
                        )}
                        {headerActions}
                    </>
                }
                footer={
                    isLiveMode && wsUrl && wsToken ? (
                        <LiveAssistant
                            wsUrl={wsUrl}
                            token={wsToken}
                            conversationId={conversationId || undefined}
                            onEnd={() => setIsLiveMode(false)}
                        />
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
                            <PendingMessageList
                                queue={messageQueue}
                                onDelete={removeQueueItem}
                            />
                            <Composer
                                ref={composerRef}
                                onSend={sendMessage}
                                disabled={false}
                                placeholder={messageQueue.length > 0 ? "Queued..." : "Type a message..."}
                                effortLevel={internalEffortLevel}
                                onEffortLevelChange={setEffortLevel}
                                webSearchEnabled={webSearchEnabled}
                                onWebSearchChange={setWebSearch}
                                pageContextEnabled={pageContextEnabled}
                                onPageContextChange={setPageContext}
                                onFileUpload={handleFileUpload}
                                replyingTo={replyingTo}
                                onDismissReply={() => setReplyingTo(null)}
                            />
                        </div>
                    )
                }
            >
                {messages.length === 0 ? (
                    <WelcomeScreen userName={userName} actions={quickActions} />
                ) : (
                    <>
                        {messages.map((msg) => (
                            <MessageBubble
                                key={msg.id}
                                {...msg}
                                shouldAnimate={!finishedMessageIds.has(msg.id)}
                                onAnimationComplete={() => handleAnimationComplete(msg.id)}
                                onConfirm={handleConfirm}
                                onReject={handleReject}
                                onToolCall={onToolCall}
                                onReply={handleReply}
                                onEdit={handleEdit}
                                onSwitchBranch={handleSwitchBranch}
                                isWaitingForDeltas={
                                    isWaitingForDeltas &&
                                    msg.role === 'assistant' &&
                                    msg.id === messages[messages.length - 1]?.id
                                }
                            />
                        ))}

                        {isThinking && (!messages.length || messages[messages.length - 1].role !== 'assistant') && (
                            <div style={{ paddingLeft: '16px' }}>
                                <ThinkingIndicator />
                            </div>
                        )}
                    </>
                )}
            </ChatContainer>
        </ChatbotProvider>
    );
};
