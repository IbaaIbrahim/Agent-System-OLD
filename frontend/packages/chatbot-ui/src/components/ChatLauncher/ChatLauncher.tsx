import React from 'react';
import './ChatLauncher.css';

interface ChatLauncherProps {
    onClick: () => void;
}

export const ChatLauncher: React.FC<ChatLauncherProps> = ({ onClick }) => {
    return (
        <button className="cb-chat-launcher" onClick={onClick}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
        </button>
    );
};
