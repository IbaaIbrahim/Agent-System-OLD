import React, { useState, useEffect } from 'react'
import {
    Chatbot,
    RealChatClient,
    AuthClient,
    ChatMode,
} from '@chatbot-ui/core'

function App() {
    const [mode, setMode] = useState<ChatMode>('sidebar');
    const [isOpen, setIsOpen] = useState(true);
    const [isEmbedded, setIsEmbedded] = useState(false);
    const [client, setClient] = useState<any>(null);

    // Initialize Auth & Client
    useEffect(() => {
        const initAuth = async () => {
            try {
                const token = await AuthClient.getInitialToken();
                setClient(new RealChatClient(token));
            } catch (error) {
                console.error('Failed to initialize auth:', error);
            }
        };
        initAuth();
    }, []);


    const quickActions = [
        { id: 'q1', label: 'What should I work on next?', icon: '💬', onClick: () => { } },
        { id: 'q2', label: 'Write an update about my week.', icon: '💬', onClick: () => { } },
        { id: 'q3', label: 'Are any of my work items overdue?', icon: '💬', onClick: () => { } }
    ];

    const demoControls = (
        <div style={{ position: isEmbedded ? 'relative' : 'absolute', top: isEmbedded ? 0 : 10, left: isEmbedded ? 0 : 10, zIndex: 10000, background: 'rgba(255,255,255,0.9)', padding: 10, borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.2)', display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' as const }}>
            <span style={{ fontWeight: 'bold', marginRight: 5 }}>Demo Controls:</span>
            <button onClick={() => { setMode('floating'); setIsOpen(true); }} style={{ cursor: 'pointer', padding: '4px 8px' }}>Floating</button>
            <button onClick={() => { setMode('sidebar'); setIsOpen(true); }} style={{ cursor: 'pointer', padding: '4px 8px' }}>Sidebar</button>
            <button onClick={() => { setMode('fullscreen'); setIsOpen(true); }} style={{ cursor: 'pointer', padding: '4px 8px' }}>Fullscreen</button>
            <button onClick={() => setIsOpen(!isOpen)} style={{ cursor: 'pointer', padding: '4px 8px' }}>Toggle View</button>
            <button onClick={() => setIsEmbedded(!isEmbedded)} style={{ cursor: 'pointer', padding: '4px 8px', background: isEmbedded ? '#4CAF50' : undefined, color: isEmbedded ? '#fff' : undefined }}>{isEmbedded ? 'Embedded' : 'Overlay'}</button>
        </div>
    );

    return (
        <div
            style={{ display: 'flex', height: '100vh', width: '100vw', fontFamily: 'sans-serif' }}
        >
            {isEmbedded ? (
                <>
                    <main style={{ flex: 1, overflow: 'auto', background: '#333', padding: 20 }}>
                        {demoControls}
                        <div style={{ color: '#ccc', marginTop: 20 }}>
                            <h2 style={{ color: '#fff' }}>Main Content Area</h2>
                            <p>This content shrinks when the chat panel opens.</p>
                        </div>
                    </main>
                    <Chatbot
                        client={client}
                        mode={mode}
                        isOpen={isOpen}
                        embedded={true}
                        onClose={() => setIsOpen(false)}
                        userName="Ibaa"
                        quickActions={quickActions}
                    />
                </>
            ) : (
                <div style={{ flex: 1, background: '#333', position: 'relative' }}>
                    {demoControls}
                    <Chatbot
                        client={client}
                        mode={mode}
                        isOpen={isOpen}
                        onClose={() => setIsOpen(false)}
                        userName="Ibaa"
                        quickActions={quickActions}
                    />
                </div>
            )}
        </div>
    )
}

export default App
