import React from 'react';
import './WelcomeScreen.css';

export interface QuickAction {
    id: string;
    label: string;
    icon?: React.ReactNode;
    onClick: () => void;
}

export interface WelcomeScreenProps {
    userName?: string;
    actions?: QuickAction[];
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({
    userName = 'User',
    actions = []
}) => {
    return (
        <div className="cb-welcome-container">
            <div className="cb-welcome-logo">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    <path d="M8 10h.01"></path>
                    <path d="M12 10h.01"></path>
                    <path d="M16 10h.01"></path>
                </svg>
            </div>
            <h2 className="cb-welcome-title">How can I help, {userName}?</h2>

            <div className="cb-quick-actions-grid">
                {actions.map(action => (
                    <button key={action.id} className="cb-quick-action-card" onClick={action.onClick}>
                        <div className="cb-action-icon">{action.icon}</div>
                        <span className="cb-action-label">{action.label}</span>
                    </button>
                ))}
            </div>
        </div>
    );
};
