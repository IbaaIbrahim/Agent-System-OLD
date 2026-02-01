import React, { useState, useEffect } from 'react';
import './MessageBubble.css';
import { ToolInvocation } from '../ToolInvocation/ToolInvocation';

export interface Attachment {
    id: string;
    type: 'image' | 'file';
    url: string;
    name?: string;
}

export interface MessageProps {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    timestamp?: Date;
    attachments?: Attachment[];
    toolInvocation?: {
        toolName: string;
        args: any;
        status: 'running' | 'completed' | 'failed';
    };
}

export const MessageBubble: React.FC<MessageProps> = ({
    role,
    content,
    attachments,
    toolInvocation
}) => {
    // Typewriter effect state
    const [displayContent, setDisplayContent] = useState('');

    // Only animate if it's an assistant message AND there is content to animate.
    // If it's a tool call (empty content usually), don't animate.
    useEffect(() => {
        if (role !== 'assistant' || !content) {
            setDisplayContent(content || '');
            return;
        }

        let currentIndex = 0;
        const speed = 15; // ms per char

        const interval = setInterval(() => {
            if (currentIndex < content.length) {
                setDisplayContent(prev => content.substring(0, currentIndex + 1));
                currentIndex++;
            } else {
                clearInterval(interval);
            }
        }, speed);

        return () => clearInterval(interval);
    }, [content, role]);

    if (toolInvocation) {
        return (
            <div className="cb-message-row cb-row-assistant" style={{ marginBottom: '8px' }}>
                <div className="cb-avatar-container">
                    <div className="cb-avatar-assistant" style={{ background: '#333' }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></svg>
                    </div>
                </div>
                <div className="cb-message-content-wrapper">
                    <ToolInvocation
                        toolName={toolInvocation.toolName}
                        args={toolInvocation.args}
                        status={toolInvocation.status}
                    />
                </div>
            </div>
        );
    }

    return (
        <div className={`cb-message-row ${role === 'user' ? 'cb-row-user' : 'cb-row-assistant'}`}>
            {role === 'assistant' && (
                <div className="cb-avatar-container">
                    <div className="cb-avatar-assistant">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 0 1 10 10c0 5.524-4.476 10-10 10S2 17.524 2 12 6.476 2 12 2z" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
                    </div>
                </div>
            )}

            <div className="cb-message-content-wrapper">
                {role === 'user' ? null : <div className="cb-sender-name">Assistant</div>}
                <div className={`cb-message-bubble ${role}`}>
                    <div className="cb-markdown-content">
                        {displayContent}
                        {role === 'assistant' && content && displayContent.length < content.length && (
                            <span className="cb-cursor">|</span>
                        )}
                    </div>
                    {attachments && attachments.length > 0 && (
                        <div className="cb-attachments-grid">
                            {/* Attachment rendering logic here */}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
