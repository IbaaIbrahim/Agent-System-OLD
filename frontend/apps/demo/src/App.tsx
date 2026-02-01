import React, { useState, useEffect, useMemo } from 'react'
import {
    ChatContainer,
    Composer,
    ChatMode,
    MessageBubble,
    ThinkingIndicator,
    ToolInvocation,
    WelcomeScreen,
    NavigationSidebar,
    SidebarItem,
    MessageProps
} from '@chatbot-ui/core'
import { MockChatClient } from './api/MockChatClient';
import { ChatState } from './api/types';

function App() {
    const [mode, setMode] = useState<ChatMode>('sidebar');
    const [isOpen, setIsOpen] = useState(true);

    // State managed by Client
    const [chatState, setChatState] = useState<ChatState>({ messages: [], isThinking: false });

    // Initialize Client (Memoized to persist across renders)
    const client = useMemo(() => new MockChatClient(), []);

    const handleSend = async (text: string) => {
        await client.sendMessage(text, setChatState);
    };

    const startNewChat = () => {
        client.reset(setChatState);
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
                <div style={{ fontSize: 12, marginTop: 4 }}>Try "Close" via X button to test Bubble</div>
            </div>

            <ChatContainer
                mode={mode}
                isOpen={isOpen}
                onClose={() => setIsOpen(false)}
                onOpen={() => setIsOpen(true)}
                drawerContent={navContent}
                footer={<Composer onSend={handleSend} />}
            >
                {/* Content is now handled by ChatContainer's scroll view */}
                {chatState.messages.length === 0 ? (
                    <WelcomeScreen userName="Ibaa" actions={quickActions} />
                ) : (
                    <>
                        {chatState.messages.map((msg, index) => (
                            <MessageBubble key={msg.id} {...msg} />
                        ))}

                        {chatState.isThinking && (
                            <div style={{ paddingLeft: '16px' }}>
                                <ThinkingIndicator />
                            </div>
                        )}
                    </>
                )}
            </ChatContainer>
        </div>
    )
}

export default App
