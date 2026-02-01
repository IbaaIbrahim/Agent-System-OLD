import React from 'react';
import './NavigationSidebar.css';

export interface SidebarItem {
    id: string;
    label: string;
    icon?: React.ReactNode;
    active?: boolean;
    onClick?: () => void;
}

export interface NavigationSidebarProps {
    onNewChat: () => void;
    agents: SidebarItem[];
    chatHistory: SidebarItem[];
}

export const NavigationSidebar: React.FC<NavigationSidebarProps> = ({
    onNewChat,
    agents,
    chatHistory
}) => {
    return (
        <div className="cb-nav-sidebar">
            <div className="cb-nav-header">
                <button className="cb-new-chat-btn" onClick={onNewChat}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    <span>New chat</span>
                </button>
            </div>

            <div className="cb-nav-section">
                <h3 className="cb-nav-title">Agents</h3>
                {agents.map(agent => (
                    <div key={agent.id} className={`cb-nav-item ${agent.active ? 'active' : ''}`} onClick={agent.onClick}>
                        <span className="cb-item-icon">{agent.icon}</span>
                        <span className="cb-item-label">{agent.label}</span>
                    </div>
                ))}
                <div className="cb-nav-item action">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="18" x2="21" y2="18" /></svg>
                    <span className="cb-item-label">View all agents</span>
                </div>
            </div>

            <div className="cb-nav-section">
                <h3 className="cb-nav-title">Chats</h3>
                {chatHistory.map(chat => (
                    <div key={chat.id} className={`cb-nav-item ${chat.active ? 'active' : ''}`} onClick={chat.onClick}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="cb-item-icon"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
                        <span className="cb-item-label">{chat.label}</span>
                    </div>
                ))}
                <div className="cb-nav-item action">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="18" x2="21" y2="18" /></svg>
                    <span className="cb-item-label">View all conversations</span>
                </div>
            </div>
        </div>
    );
};
