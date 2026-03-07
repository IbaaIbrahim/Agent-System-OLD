import React from 'react';
const { useState, useEffect, useRef, useLayoutEffect } = React;
import './ChatContainer.css';
import { ChatLauncher } from '../ChatLauncher/ChatLauncher';

export type ChatMode = 'floating' | 'sidebar' | 'fullscreen';

export interface ChatContainerProps {
    mode?: ChatMode;
    isOpen?: boolean;
    embedded?: boolean;
    onClose?: () => void;
    onOpen?: () => void;
    children?: React.ReactNode;
    drawerContent?: React.ReactNode;
    footer?: React.ReactNode;
    isDrawerOpen?: boolean;
    onDrawerOpenChange?: (isOpen: boolean) => void;
    headerActions?: React.ReactNode;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
    mode = 'floating',
    isOpen = true,
    embedded = false,
    onClose,
    onOpen,
    children,
    drawerContent,
    footer,
    isDrawerOpen: controlledIsDrawerOpen,
    onDrawerOpenChange,
    headerActions
}) => {
    const [mounted, setMounted] = useState(false);
    const [internalIsDrawerOpen, setInternalIsDrawerOpen] = useState(false);

    const isDrawerOpen = controlledIsDrawerOpen !== undefined ? controlledIsDrawerOpen : internalIsDrawerOpen;

    const setIsDrawerOpen = (open: boolean) => {
        setInternalIsDrawerOpen(open);
        onDrawerOpenChange?.(open);
    };

    const [showScrollBtn, setShowScrollBtn] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);
    const [scrollProgress, setScrollProgress] = useState(0);
    const [isAtBottom, setIsAtBottom] = useState(true);
    const isProgrammaticScroll = useRef(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    // Instant scroll for auto-scroll during streaming (no animation conflicts)
    const scrollToBottomInstant = () => {
        if (scrollRef.current) {
            isProgrammaticScroll.current = true;
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            requestAnimationFrame(() => {
                isProgrammaticScroll.current = false;
            });
        }
    };

    // Smooth scroll for manual "scroll to bottom" button — also re-enables auto-scroll
    const scrollToBottomSmooth = () => {
        setIsAtBottom(true);
        scrollEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    const handleScroll = () => {
        if (!scrollRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
        const distFromBottom = scrollHeight - scrollTop - clientHeight;

        // Progress Calculation
        const totalScrollable = scrollHeight - clientHeight;
        const progress = totalScrollable > 0 ? (scrollTop / totalScrollable) * 100 : 0;
        setScrollProgress(progress);

        // Show button if we are more than 100px from bottom
        setShowScrollBtn(distFromBottom > 100);

        // Only update isAtBottom on user-initiated scrolls
        if (!isProgrammaticScroll.current) {
            setIsAtBottom(distFromBottom < 50);
        }
    };

    const contentRef = useRef<HTMLDivElement>(null);
    const scrollEndRef = useRef<HTMLDivElement>(null);

    // Whenever children change (new messages), if we were at bottom, scroll to bottom
    useLayoutEffect(() => {
        if (isAtBottom) {
            scrollToBottomInstant();
        }
    }, [children, isAtBottom]);

    // Sticky Scroll: Observe content height changes (Typewriter effect)
    useLayoutEffect(() => {
        if (!contentRef.current) return;

        let rafId: number | null = null;

        const observer = new ResizeObserver(() => {
            if (isAtBottom) {
                if (rafId !== null) {
                    cancelAnimationFrame(rafId);
                }
                rafId = requestAnimationFrame(() => {
                    scrollToBottomInstant();
                    rafId = null;
                });
            }
        });

        observer.observe(contentRef.current);

        return () => {
            observer.disconnect();
            if (rafId !== null) {
                cancelAnimationFrame(rafId);
            }
        };
    }, [isAtBottom]);

    if (!mounted) return null;


    if (!isOpen && mode === 'floating' && !embedded) {
        return <ChatLauncher onClick={() => onOpen?.()} />;
    }

    const containerClasses = [
        'cb-chat-container',
        `cb-mode-${mode}`,
        isOpen ? 'cb-open' : 'cb-closed',
        embedded ? 'cb-embedded' : null
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
                    {headerActions}
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
                        {/* Scroll Progress Indicator */}
                        <div className="cb-scroll-progress-container">
                            <div
                                className="cb-scroll-progress-bar"
                                style={{ width: `${scrollProgress}%` }}
                            />
                        </div>
                        <div className={`cb-scroll-shadow-top ${scrollProgress > 5 ? 'visible' : ''}`} />
                        {/* Wrapped Scroll View */}
                        <div
                            className="cb-scroll-view"
                            ref={scrollRef}
                            onScroll={handleScroll}
                        >
                            <div ref={contentRef}>
                                {children}
                                <div ref={scrollEndRef} />
                            </div>
                        </div>
                        <div className={`cb-scroll-shadow-bottom ${!isAtBottom ? 'visible' : ''}`} />

                        {/* Scroll To Bottom Button */}
                        <button
                            className={`cb-scroll-bottom-btn ${showScrollBtn ? 'visible' : ''}`}
                            onClick={scrollToBottomSmooth}
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
