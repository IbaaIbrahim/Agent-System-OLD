import React from 'react';
import './PendingMessageList.css';

interface PendingMessageListProps {
    queue: string[];
}

export const PendingMessageList: React.FC<PendingMessageListProps> = ({ queue }) => {
    if (queue.length === 0) return null;

    return (
        <div className="cb-pending-messages">
            {queue.map((msg, index) => (
                <div key={index} className="cb-pending-item">
                    <span className="cb-pending-text">{msg}</span>
                    <span className="cb-pending-status">
                        {index === 0 ? 'Wait...' : 'Queued'}
                    </span>
                </div>
            ))}
        </div>
    );
};
