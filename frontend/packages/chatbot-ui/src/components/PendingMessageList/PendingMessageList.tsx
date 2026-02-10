import React from 'react';
const { useState } = React;
import './PendingMessageList.css';

interface PendingMessageListProps {
    queue: string[];
    onDelete?: (index: number) => void;
}

export const PendingMessageList: React.FC<PendingMessageListProps> = ({ queue, onDelete }) => {
    const [isExpanded, setIsExpanded] = useState(false);

    if (queue.length === 0) return null;

    const toggleExpand = () => setIsExpanded(!isExpanded);

    return (
        <div className={`cb-pending-messages ${isExpanded ? 'expanded' : 'collapsed'}`}>
            {/* Integrated Header / collapsed view */}
            <div className="cb-pending-header" onClick={toggleExpand}>
                <div className="cb-pending-toggle-icon">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="9 18 15 12 9 6"></polyline>
                    </svg>
                </div>

                {/* Preview when collapsed */}
                <div className="cb-pending-preview">
                    <span className="cb-pending-badge">{queue.length}</span>
                    <span className="cb-pending-preview-text">
                        {isExpanded ? 'Pending Messages' : queue[0]}
                    </span>
                </div>
            </div>

            {/* List only visible when expanded */}
            {isExpanded && (
                <div className="cb-pending-list">
                    {queue.map((msg, index) => (
                        <div key={index} className="cb-pending-item">
                            <div className="cb-pending-content">
                                <span className="cb-status-label">
                                    {index === 0 ? 'NEXT' : 'WAIT'}
                                </span>
                                <span className="cb-pending-text" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg}</span>
                            </div>
                            {onDelete && (
                                <button
                                    className="cb-pending-remove"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onDelete(index);
                                    }}
                                    title="Remove"
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <line x1="18" y1="6" x2="6" y2="18"></line>
                                        <line x1="6" y1="6" x2="18" y2="18"></line>
                                    </svg>
                                </button>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
