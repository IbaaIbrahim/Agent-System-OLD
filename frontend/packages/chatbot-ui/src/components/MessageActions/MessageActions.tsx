import React, { useState } from 'react';
import './MessageActions.css';

interface MessageActionsProps {
    content: string;
    role: 'user' | 'assistant';
    messageId: string;
    onReply?: () => void;
    onEdit?: () => void;
}

export const MessageActions: React.FC<MessageActionsProps> = ({
    content,
    role,
    messageId,
    onReply,
    onEdit,
}) => {
    const [copied, setCopied] = useState(false);

    const handleCopy = async (e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            await navigator.clipboard.writeText(content);
        } catch {
            // Fallback for older browsers
            const textarea = document.createElement('textarea');
            textarea.value = content;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="cb-message-actions">
            <button
                className="cb-action-btn"
                onClick={handleCopy}
                title={copied ? 'Copied!' : 'Copy'}
            >
                {copied ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20 6 9 17 4 12" />
                    </svg>
                ) : (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                    </svg>
                )}
            </button>

            {onReply && (
                <button
                    className="cb-action-btn"
                    onClick={(e) => { e.stopPropagation(); onReply(); }}
                    title="Reply"
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="9 17 4 12 9 7" />
                        <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                    </svg>
                </button>
            )}

            {onEdit && role === 'user' && (
                <button
                    className="cb-action-btn"
                    onClick={(e) => { e.stopPropagation(); onEdit(); }}
                    title="Edit"
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                    </svg>
                </button>
            )}
        </div>
    );
};
