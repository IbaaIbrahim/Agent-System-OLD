import React from 'react';
import { ChatContainer, ChatMode } from '../ChatContainer/ChatContainer';
import { MessageBubble } from '../MessageBubble/MessageBubble';
import { Composer } from '../Composer/Composer';
import { NavigationSidebar, SidebarItem } from '../NavigationSidebar/NavigationSidebar';
import { WelcomeScreen } from '../WelcomeScreen/WelcomeScreen';
import { PendingMessageList } from '../PendingMessageList/PendingMessageList';
import { ThinkingIndicator } from '../ThinkingIndicator/ThinkingIndicator';
import { useChat } from '../../hooks/useChat';
import { ChatClient } from '../../api/types';

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
    chatHistory = [],
    onToolCall,
    headerActions,
    webSearchEnabled: controlledWebSearch,
    onWebSearchChange: controlledOnWebSearch,
    pageContextEnabled: controlledPageContext,
    onPageContextChange: controlledOnPageContext,
}) => {
    const [isDrawerOpen, setIsDrawerOpen] = React.useState(false);
    const [isReadingPage, setIsReadingPage] = React.useState(false);
    const clientRef = React.useRef(client);
    clientRef.current = client;

    // Internal state for toggles if not controlled
    const [internalWebSearch, setInternalWebSearch] = React.useState(false);
    const [internalPageContext, setInternalPageContext] = React.useState(false);

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

    const {
        messages,
        isThinking,
        messageQueue,
        sendMessage,
        removeQueueItem,
        handleAnimationComplete,
        finishedMessageIds,
        reset
    } = useChat({ client, onToolCall });

    const handleConfirm = (toolCallId: string) => {
        client?.sendConfirmResponse?.(toolCallId, true);
    };

    const handleReject = (toolCallId: string) => {
        client?.sendConfirmResponse?.(toolCallId, false);
    };

    const startNewChat = () => {
        reset();
        setIsDrawerOpen(false);
    };

    // Verify client prop on every render
    console.log('[Chatbot] Render, client:', !!client, clientRef.current ? 'Ref has value' : 'Ref is null');

    const handleFileUpload = React.useCallback(async (file: File) => {
        const c = clientRef.current;
        console.log('[Chatbot] handleFileUpload called, clientRef:', !!c);

        if (!c) {
            throw new Error('Chat client not initialized');
        }
        // Call uploadFile - it exists on RealChatClient
        const uploadFn = (c as any).uploadFile;
        if (typeof uploadFn !== 'function') {
            throw new Error('File upload not supported by this client');
        }
        return uploadFn.call(c, file);
    }, []);

    const navContent = (
        <NavigationSidebar
            onNewChat={startNewChat}
            agents={agents}
            chatHistory={chatHistory}
        />
    );

    return (
        <>
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
                headerActions={headerActions}
                footer={
                    <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
                        <PendingMessageList
                            queue={messageQueue}
                            onDelete={removeQueueItem}
                        />
                        <Composer
                            onSend={sendMessage}
                            disabled={false}
                            placeholder={messageQueue.length > 0 ? "Queued..." : "Type a message..."}
                            webSearchEnabled={webSearchEnabled}
                            onWebSearchChange={setWebSearch}
                            pageContextEnabled={pageContextEnabled}
                            onPageContextChange={setPageContext}
                            onFileUpload={handleFileUpload}
                        />
                    </div>
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
        </>
    );
};
