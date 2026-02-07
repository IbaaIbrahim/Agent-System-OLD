import React, { useState, useEffect, useMemo, useRef } from 'react'
import {
    ChatContainer,
    Composer,
    ChatMode,
    MessageBubble,
    ThinkingIndicator,
    WelcomeScreen,
    NavigationSidebar,
    SidebarItem,
    MessageProps,
    PendingMessageList
} from '@chatbot-ui/core'
import { RealChatClient } from './api/RealChatClient';
import { AuthClient } from './api/AuthClient';
import { ChatState, ChatClient } from './api/types';

function App() {
    const [mode, setMode] = useState<ChatMode>('sidebar');
    const [isOpen, setIsOpen] = useState(true);
    const [selectedModel, setSelectedModel] = useState<string>('auto');
    const [isConfigOpen, setIsConfigOpen] = useState(false);


    // State managed by Client
    const [chatState, setChatState] = useState<ChatState>({ messages: [], isThinking: false });

    // User Message Queue Logic
    const [messageQueue, setMessageQueue] = useState<string[]>([]);
    const [isProcessing, setIsProcessing] = useState(false);
    const [isTyping, setIsTyping] = useState(false);

    // Auth & Client State
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [client, setClient] = useState<ChatClient | null>(null);

    // Robustly track which messages have finished animating to prevent race conditions
    const finishedMessageIdsRef = useRef<Set<string>>(new Set());

    // Initialize Auth & Client
    useEffect(() => {
        const initAuth = async () => {
            try {
                console.log('Fetching initial token...');
                const token = await AuthClient.getInitialToken();
                console.log('Token received:', token);
                setAccessToken(token);
                setClient(new RealChatClient(token));
            } catch (error) {
                console.error('Failed to initialize auth:', error);
                // Fallback to Mock? Or just show error? 
                // For now, we leave client as null, blocking chat.
            }
        };

        initAuth();

        // Refresh token every 45 minutes
        const REFRESH_INTERVAL = 45 * 60 * 1000;
        const intervalId = setInterval(async () => {
            try {
                console.log('Refreshing token...');
                const newToken = await AuthClient.refreshToken();
                setAccessToken(newToken);
                // Update client with new token if it's an instance of RealChatClient
                setClient(prev => {
                    if (prev instanceof RealChatClient) {
                        prev.setToken(newToken);
                        return prev;
                    }
                    return new RealChatClient(newToken);
                });
            } catch (error) {
                console.error('Failed to refresh token:', error);
            }
        }, REFRESH_INTERVAL);

        return () => clearInterval(intervalId);
    }, []);

    // Sync Model to Client
    useEffect(() => {
        if (client) {
            client.setModel(selectedModel);
        }
    }, [client, selectedModel]);


    // Watch queue and processing state
    useEffect(() => {
        const processQueue = async () => {
            // 1. Basic Locks
            if (!client) return; // Wait for client to be ready
            if (messageQueue.length === 0) return;
            if (chatState.isThinking) return;

            // 2. Strict Typing Lock
            // Check if the very last message is an assistant message that hasn't finished typing.
            const lastMsg = chatState.messages[chatState.messages.length - 1];
            if (lastMsg?.role === 'assistant') {
                // Check if it has content EITHER in legacy .content OR in the last step .content
                const hasContent = lastMsg.content || (lastMsg.steps && lastMsg.steps.length > 0 && lastMsg.steps[lastMsg.steps.length - 1].type === 'text');

                if (hasContent) {
                    if (!finishedMessageIdsRef.current.has(lastMsg.id)) {
                        // It hasn't finished typing.
                        // We must wait.
                        if (!isTyping) setIsTyping(true);
                        return;
                    }
                }
            }

            // Take next message
            const nextMessage = messageQueue[0];
            setIsProcessing(true); // Lock

            // Remove from queue
            setMessageQueue(prev => prev.slice(1));

            try {
                await client.sendMessage(nextMessage, setChatState);
            } finally {
                setIsProcessing(false); // Unlock for next turn
            }
        };

        processQueue();
    }, [messageQueue, isProcessing, isTyping, chatState.isThinking, chatState.messages]);

    // Track when AI starts typing to lock the queue - Visual Sync only
    useEffect(() => {
        if (chatState.messages.length > prevMsgCountRef.current) {
            const lastMsg = chatState.messages[chatState.messages.length - 1];
            // If it's an assistant message with content (legacy or steps), it will trigger typewriter, so we lock.
            const hasContent = lastMsg.role === 'assistant' && (lastMsg.content || (lastMsg.steps && lastMsg.steps.length > 0 && lastMsg.steps[lastMsg.steps.length - 1].type === 'text'));

            if (hasContent) {
                setIsTyping(true);
            }
        }
        prevMsgCountRef.current = chatState.messages.length;
    }, [chatState.messages]);

    const handleAnimationComplete = (id: string) => {
        finishedMessageIdsRef.current.add(id);
        setIsTyping(false); // Triggers the effect to check queue again
    };

    const prevMsgCountRef = useRef(0);

    // Initialize Client (Memoized to persist across renders)
    // REMOVED: const client = useMemo(() => new MockChatClient(), []);

    const handleSend = (text: string) => {
        // Enqueue message
        setMessageQueue(prev => [...prev, text]);
    };

    const startNewChat = () => {
        if (client) client.reset(setChatState);
        setMessageQueue([]);
        setIsProcessing(false);
        setIsTyping(false);
        finishedMessageIdsRef.current.clear();
        prevMsgCountRef.current = 0;
    };

    // When chat is closed, mark all messages as finished animating
    // so they display immediately when re-opened.
    useEffect(() => {
        if (!isOpen) {
            chatState.messages.forEach(msg => {
                finishedMessageIdsRef.current.add(msg.id);
            });
            // Also ensure typing state is reset if we force finish
            if (isTyping) setIsTyping(false);
        }
    }, [isOpen, chatState.messages, isTyping]);

    const handleRemoveQueueItem = (index: number) => {
        console.log('Removing item at index:', index);
        setMessageQueue(prev => {
            const newQueue = [...prev];
            newQueue.splice(index, 1);
            return newQueue;
        });
    };

    const agents: SidebarItem[] = [
        { id: '1', label: 'Bug Report Assistant', icon: '🐞' },
        { id: '2', label: 'Comms Crafter', icon: '📝' },
        { id: '3', label: 'Service Triage', icon: '🔧' }
    ];

    const history: SidebarItem[] = [
        { id: 'h1', label: 'What should I work on next?' },
        { id: 'h2', label: 'Write an update about my week.' },
        { id: 'h3', label: 'Are any of my work items overdue?' }
    ];

    const quickActions = [
        { id: 'q1', label: 'What should I work on next?', icon: '💬', onClick: () => handleSend('What should I work on next?') },
        { id: 'q2', label: 'Write an update about my week.', icon: '💬', onClick: () => handleSend('Write an update for the week') },
        { id: 'q3', label: 'Are any of my work items overdue?', icon: '💬', onClick: () => handleSend('Check overdue items') }
    ];

    const navContent = (
        <NavigationSidebar
            onNewChat={startNewChat}
            agents={agents}
            chatHistory={history}
        />
    );

    return (
        <div style={{ padding: '0', fontFamily: 'sans-serif', background: '#333', height: '100vh', width: '100vw' }}>
            {/* Controls for Demo */}
            <div style={{ position: 'absolute', top: 10, left: 10, zIndex: 10000, background: 'rgba(255,255,255,0.9)', padding: 10, borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.2)', display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontWeight: 'bold', marginRight: 5 }}>Demo Controls:</span>
                <button onClick={() => { setMode('floating'); setIsOpen(true); }} style={{ cursor: 'pointer', padding: '4px 8px' }}>Floating</button>
                <button onClick={() => { setMode('sidebar'); setIsOpen(true); }} style={{ cursor: 'pointer', padding: '4px 8px' }}>Sidebar</button>
                <button onClick={() => { setMode('fullscreen'); setIsOpen(true); }} style={{ cursor: 'pointer', padding: '4px 8px' }}>Fullscreen</button>
                <button onClick={() => setIsOpen(!isOpen)} style={{ cursor: 'pointer', padding: '4px 8px' }}>Toggle View</button>

                <div style={{ borderLeft: '1px solid #ccc', height: '20px', margin: '0 5px' }}></div>

                <label style={{ fontSize: '14px' }}>Model: </label>
                <select
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                    style={{ padding: '4px', borderRadius: '4px', border: '1px solid #ccc' }}
                >
                    <option value="auto">Auto (Default)</option>
                    <option value="gpt-4o">GPT-4o</option>
                    <option value="gpt-4o-mini">GPT-4o Mini</option>
                    <option value="claude-3-5-sonnet-20240620">Claude 3.5 Sonnet</option>
                </select>
            </div>


            <ChatContainer
                mode={mode}
                isOpen={isOpen}
                onClose={() => setIsOpen(false)}
                onOpen={() => setIsOpen(true)}
                drawerContent={navContent}
                footer={
                    <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
                        <PendingMessageList
                            queue={messageQueue}
                            onDelete={handleRemoveQueueItem}
                        />
                        <Composer
                            onSend={handleSend}
                            disabled={false}
                            placeholder={messageQueue.length > 0 ? "Queued..." : "Type a message..."}
                        />
                    </div>
                }
            >
                {chatState.messages.length === 0 ? (
                    <WelcomeScreen userName="Ibaa" actions={quickActions} />
                ) : (
                    <>
                        {chatState.messages.map((msg) => (
                            <MessageBubble
                                key={msg.id}
                                {...msg}
                                shouldAnimate={!finishedMessageIdsRef.current.has(msg.id)}
                                onAnimationComplete={() => handleAnimationComplete(msg.id)}
                            />
                        ))}

                        {(chatState.isThinking || isProcessing) && (
                            /* Only show global thinking if we don't have an assistant message at the end yet.
                               If we DO have one, it likely has its own internal thinking indicator. */
                            (!chatState.messages.length || chatState.messages[chatState.messages.length - 1].role !== 'assistant') && (
                                <div style={{ paddingLeft: '16px' }}>
                                    <ThinkingIndicator />
                                </div>
                            )
                        )}
                    </>
                )}
            </ChatContainer>
        </div>
    )
}

export default App
