import React from 'react';
import './ReplyPreview.css';

interface ReplyPreviewProps {
    role: string;
    content: string;
    onDismiss: () => void;
}

export const ReplyPreview: React.FC<ReplyPreviewProps> = ({ role, content, onDismiss }) => {
    const snippet = content.length > 120 ? content.slice(0, 120) + '...' : content;

    return (
        <div className="cb-reply-preview">
            <div className="cb-reply-preview-bar" />
            <div className="cb-reply-preview-content">
                <span className="cb-reply-preview-label">
                    Replying to {role === 'user' ? 'yourself' : 'Assistant'}
                </span>
                <span className="cb-reply-preview-snippet">{snippet}</span>
            </div>
            <button
                className="cb-reply-preview-dismiss"
                onClick={onDismiss}
                title="Cancel reply"
            >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
            </button>
        </div>
    );
};
