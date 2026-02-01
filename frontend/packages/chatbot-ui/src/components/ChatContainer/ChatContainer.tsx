import React, { useState, useEffect, useRef, useLayoutEffect } from 'react';
import './ChatContainer.css';
import { ChatLauncher } from '../ChatLauncher/ChatLauncher';

export type ChatMode = 'floating' | 'sidebar' | 'fullscreen';

export interface ChatContainerProps {
    mode?: ChatMode;
    isOpen?: boolean;
    onClose?: () => void;
    onOpen?: () => void;
    children?: React.ReactNode;
    drawerContent?: React.ReactNode;
    footer?: React.ReactNode;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
    mode = 'floating',
    isOpen = true,
    onClose,
    onOpen,
    children,
    drawerContent,
    footer
}) => {
    const [mounted, setMounted] = useState(false);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);
    const [isAtBottom, setIsAtBottom] = useState(true);

    useEffect(() => {
        setMounted(true);
    }, []);

    // Auto-scroll logic: Check if we should stick to bottom on content change
    const scrollToBottom = () => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    };

    const handleScroll = () => {
        if (!scrollRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
        const distFromBottom = scrollHeight - scrollTop - clientHeight;

        // Show button if we are more than 100px from bottom
        setShowScrollBtn(distFromBottom > 100);

        // Track if we are "at bottom" (allow some margin of error)
        setIsAtBottom(distFromBottom < 50);
    };

    const contentRef = useRef<HTMLDivElement>(null);

    // Whenever children change (new messages), if we were at bottom, scroll to bottom
    useLayoutEffect(() => {
        if (isAtBottom) {
            scrollToBottom();
        }
    }, [children, isAtBottom]);

    // Sticky Scroll: Observe content height changes (Typewriter effect)
    useLayoutEffect(() => {
        if (!contentRef.current) return;

        const observer = new ResizeObserver(() => {
            if (isAtBottom) {
                scrollToBottom();
            }
        });

        observer.observe(contentRef.current);

        return () => observer.disconnect();
    }, [isAtBottom]);

    // Listen for size changes (e.g. Typewriter effect expanding a message)
    useEffect(() => {
        const currentRef = scrollRef.current;
        if (!currentRef) return;

        const observer = new ResizeObserver(() => {
            // If we were at the bottom (or very close), keep sticking to bottom
            // We increase tolerance here because the typewriter effect is fast
            if (isAtBottom) {
                scrollToBottom();
            }
        });

        // Observe the children of the scroll view to detect height changes
        // We can observe the scroll view itself, or better, its first child wrapper if it existed.
        // Since children are direct, we observe the container's scrollHeight indirectly by observing the container? 
        // No, ResizeObserver on the container fires when container resizes. 
        // MutationObserver is better for content changes, OR observing a wrapper div.
        // Let's wrap children in a div to observe it.
        // For now, let's try observing the scrollRef, but that tracks container size.
        // We need to wrap the messages in a div to observe their total height.
    });

    // Better Approach:
    // We will wrap {children} in a div below and ref IT.

    if (!mounted) return null;

    if (!isOpen && mode === 'floating') {
        return <ChatLauncher onClick={() => onOpen?.()} />;
    }

    const containerClasses = [
        'cb-chat-container',
        `cb-mode-${mode}`,
        isOpen ? 'cb-open' : 'cb-closed'
    ].filter(Boolean).join(' ');

    return (
        <div className={containerClasses}>
            <div className="cb-chat-header">
                <div className="cb-header-left">
                    <button className="cb-header-btn" onClick={() => setIsDrawerOpen(!isDrawerOpen)}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="3" y1="12" x2="21" y2="12"></line>
                            <line x1="3" y1="6" x2="21" y2="6"></line>
                            <line x1="3" y1="18" x2="21" y2="18"></line>
                        </svg>
                    </button>
                    <span className="cb-brand">AI Assistant</span>
                </div>
                <div className="cb-actions">
                    <button className="cb-minimize-btn" onClick={onClose}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M18 6L6 18M6 6l12 12" />
                        </svg>
                    </button>
                </div>
            </div>

            <div className="cb-chat-body-wrapper">
                <div className={`cb-drawer-wrapper ${isDrawerOpen ? 'open' : ''}`}>
                    <div className="cb-drawer-content">
                        {drawerContent}
                    </div>
                    <div className="cb-drawer-backdrop" onClick={() => setIsDrawerOpen(false)}></div>
                </div>

                <div className="cb-chat-content">
                    {/* Messages Area (Flex 1, contains scroll view + button) */}
                    <div className="cb-messages-area">
                        {/* Wrapped Scroll View */}
                        <div
                            className="cb-scroll-view"
                            ref={scrollRef}
                            onScroll={handleScroll}
                        >
                            <div ref={contentRef}>
                                {children}
                            </div>
                        </div>

                        {/* Scroll To Bottom Button */}
                        <button
                            className={`cb-scroll-bottom-btn ${showScrollBtn ? 'visible' : ''}`}
                            onClick={scrollToBottom}
                        >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M19 12l-7 7-7-7" /></svg>
                        </button>
                    </div>

                    {/* Fixed Footer */}
                    {footer && (
                        <div className="cb-chat-footer">
                            {footer}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
