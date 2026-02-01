import React, { useState } from 'react'
import {
    ChatContainer,
    Composer,
    ChatMode,
    MessageBubble,
    MessageProps,
    ThinkingIndicator,
    ToolInvocation,
    WelcomeScreen,
    NavigationSidebar,
    SidebarItem
} from '@chatbot-ui/core'

function App() {
    const [mode, setMode] = useState<ChatMode>('sidebar');
    const [isOpen, setIsOpen] = useState(true);
    const [messages, setMessages] = useState<MessageProps[]>([]);
    const [isThinking, setIsThinking] = useState(false);

    const handleSend = (text: string) => {
        const newUserMsg: MessageProps = { id: Date.now().toString(), role: 'user', content: text };
        setMessages(prev => [...prev, newUserMsg]);
        setIsThinking(true);

        // Simulate flow
        setTimeout(() => {
            setIsThinking(false);

            // Add Tool Invocation Message
            const toolMsgId = Date.now().toString();
            const toolMsg: MessageProps = {
                id: toolMsgId,
                role: 'assistant',
                content: '',
                toolInvocation: {
                    toolName: 'search_web',
                    args: { query: 'relevant info' },
                    status: 'running'
                }
            };
            setMessages(prev => [...prev, toolMsg]);

            setTimeout(() => {
                // Update Tool to Completed
                setMessages(prev => prev.map(m =>
                    m.id === toolMsgId
                        ? { ...m, toolInvocation: { ...m.toolInvocation!, status: 'completed' as const } }
                        : m
                ));

                // Add Answer
                const aiMsg: MessageProps = {
                    id: (Date.now() + 1).toString(),
                    role: 'assistant',
                    content: 'Based on your request, I found some relevant information...'
                };
                setMessages(prev => [...prev, aiMsg]);
            }, 2000);
        }, 1500);
    };

    const startNewChat = () => {
        setMessages([]);
        setIsThinking(false);
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
                {messages.length === 0 ? (
                    <WelcomeScreen userName="Ibaa" actions={quickActions} />
                ) : (
                    <>
                        {messages.map((msg, index) => (
                            <MessageBubble key={msg.id} {...msg} />
                        ))}

                        {isThinking && (
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
