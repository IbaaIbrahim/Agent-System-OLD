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
import { MockChatClient } from './api/MockChatClient';
import { ChatState } from './api/types';

function App() {
    const [mode, setMode] = useState<ChatMode>('sidebar');
    const [isOpen, setIsOpen] = useState(true);

    // State managed by Client
    const [chatState, setChatState] = useState<ChatState>({ messages: [], isThinking: false });

    // User Message Queue Logic
    const [messageQueue, setMessageQueue] = useState<string[]>([]);
    const [isProcessing, setIsProcessing] = useState(false);
    const [isTyping, setIsTyping] = useState(false);

    // Robustly track which messages have finished animating to prevent race conditions
    const finishedMessageIdsRef = useRef<Set<string>>(new Set());

    // Watch queue and processing state
    useEffect(() => {
        const processQueue = async () => {
            // 1. Basic Locks
            if (isProcessing) return;
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
    const client = useMemo(() => new MockChatClient(), []);

    const handleSend = (text: string) => {
        // Enqueue message
        setMessageQueue(prev => [...prev, text]);
    };

    const startNewChat = () => {
        client.reset(setChatState);
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
            <div style={{ position: 'absolute', top: 10, left: 10, zIndex: 10000, background: 'white', padding: 5, borderRadius: 4 }}>
                <button onClick={() => { setMode('floating'); setIsOpen(true); }}>Floating</button>
                <button onClick={() => { setMode('sidebar'); setIsOpen(true); }}>Sidebar</button>
                <button onClick={() => { setMode('fullscreen'); setIsOpen(true); }}>Fullscreen</button>
                <button onClick={() => setIsOpen(!isOpen)}>Toggle Open/Close</button>
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
