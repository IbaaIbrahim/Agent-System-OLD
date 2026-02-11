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
    onSearch?: (query: string) => void;
    searchResults?: SidebarItem[];
    isSearching?: boolean;
}

export const NavigationSidebar: React.FC<NavigationSidebarProps> = ({
    onNewChat,
    agents,
    chatHistory,
    onSearch,
    searchResults,
    isSearching = false,
}) => {
    const [searchQuery, setSearchQuery] = React.useState('');

    const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const value = e.target.value;
        setSearchQuery(value);
        onSearch?.(value);
    };

    const displayItems = searchQuery.trim() ? (searchResults || []) : chatHistory;

    return (
        <div className="cb-nav-sidebar">
            <div className="cb-nav-header">
                <button className="cb-new-chat-btn" onClick={onNewChat}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    <span>New chat</span>
                </button>
            </div>

            {agents.length > 0 && (
                <div className="cb-nav-section">
                    <h3 className="cb-nav-title">Agents</h3>
                    {agents.map(agent => (
                        <div key={agent.id} className={`cb-nav-item ${agent.active ? 'active' : ''}`} onClick={agent.onClick}>
                            <span className="cb-item-icon">{agent.icon}</span>
                            <span className="cb-item-label">{agent.label}</span>
                        </div>
                    ))}
                </div>
            )}

            <div className="cb-nav-section cb-nav-chats-section">
                <h3 className="cb-nav-title">Chats</h3>

                {onSearch && (
                    <div className="cb-sidebar-search">
                        <svg className="cb-sidebar-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                        </svg>
                        <input
                            type="text"
                            placeholder="Search conversations..."
                            value={searchQuery}
                            onChange={handleSearchChange}
                            className="cb-sidebar-search-input"
                        />
                    </div>
                )}

                {isSearching && (
                    <div className="cb-nav-item cb-nav-searching">Searching...</div>
                )}

                {displayItems.map(chat => (
                    <div key={chat.id} className={`cb-nav-item ${chat.active ? 'active' : ''}`} onClick={chat.onClick}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="cb-item-icon"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
                        <span className="cb-item-label">{chat.label}</span>
                    </div>
                ))}

                {!isSearching && displayItems.length === 0 && (
                    <div className="cb-nav-item cb-nav-empty">
                        {searchQuery.trim() ? 'No results found' : 'No conversations yet'}
                    </div>
                )}
            </div>
        </div>
    );
};
