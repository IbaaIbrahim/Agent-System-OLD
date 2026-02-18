import React from 'react';
const { useState, useEffect, useRef } = React;
import './MessageBubble.css';
import { ToolInvocation } from '../ToolInvocation/ToolInvocation';
import { ConfirmButtons, ConfirmStatus } from '../ConfirmButtons/ConfirmButtons';
import { AuthenticatedImage } from '../AuthenticatedImage/AuthenticatedImage';
import { BlinkingIndicator } from '../BlinkingIndicator/BlinkingIndicator';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export interface Attachment {
    id: string;
    type: 'image' | 'file';
    url: string;
    name?: string;
    size?: number;
    contentType?: string;
    localUrl?: string;
}

export type MessageStepType = 'text' | 'thinking' | 'tool-call' | 'confirm-request';

export interface MessageStep {
    id: string;
    type: MessageStepType;
    content?: string;
    toolName?: string;
    toolArgs?: any;
    toolStatus?: 'running' | 'completed' | 'failed';
    toolResult?: any;
    isFinished?: boolean;
    thoughts?: string[];
    // Confirm request fields
    toolCallId?: string;
    confirmLabel?: string;
    confirmDescription?: string;
    confirmStatus?: ConfirmStatus;
}

export interface MessageProps {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content?: string;
    steps?: MessageStep[];
    timestamp?: Date;
    attachments?: Attachment[];
    toolInvocation?: {
        toolName: string;
        args: any;
        status: 'running' | 'completed' | 'failed';
        result?: any;
    };
    onAnimationComplete?: () => void;
    shouldAnimate?: boolean;
    onConfirm?: (toolCallId: string) => void;
    onReject?: (toolCallId: string) => void;
    onToolCall?: Record<string, (data: any) => Promise<any> | void>;
    isWaitingForDeltas?: boolean; // Show blinking indicator when waiting for deltas
}

const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const getFileIcon = (contentType?: string): string => {
    if (!contentType) return '\u{1F4CE}';
    if (contentType.startsWith('image/')) return '\u{1F5BC}\u{FE0F}';
    if (contentType === 'application/pdf') return '\u{1F4C4}';
    if (contentType.startsWith('text/')) return '\u{1F4DD}';
    return '\u{1F4CE}';
};

const MAX_VISIBLE_ATTACHMENTS = 2;

const AttachmentItem: React.FC<{ att: Attachment; onClick?: () => void }> = ({ att, onClick }) => (
    att.type === 'image' ? (
        <div className="cb-msg-attachment-image" onClick={onClick} style={{ cursor: onClick ? 'pointer' : undefined }}>
            {att.localUrl ? (
                <img
                    src={att.localUrl}
                    alt={att.name || 'Attached image'}
                    onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                        target.parentElement?.classList.add('cb-msg-attachment-broken');
                    }}
                />
            ) : (
                <AuthenticatedImage
                    src={att.url}
                    alt={att.name || 'Attached image'}
                    onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                        target.parentElement?.classList.add('cb-msg-attachment-broken');
                    }}
                />
            )}
            {att.name && <span className="cb-msg-att-name">{att.name}</span>}
        </div>
    ) : (
        <div className="cb-msg-attachment-file" onClick={onClick} style={{ cursor: onClick ? 'pointer' : undefined }}>
            <span className="cb-msg-att-icon">{getFileIcon(att.contentType)}</span>
            <span className="cb-msg-att-name">{att.name}</span>
            {att.size != null && <span className="cb-msg-att-size">{formatFileSize(att.size)}</span>}
        </div>
    )
);

const AttachmentModal: React.FC<{
    attachments: Attachment[];
    isOpen: boolean;
    onClose: () => void;
}> = ({ attachments, isOpen, onClose }) => {
    if (!isOpen) return null;

    return (
        <div className="cb-attachment-modal-overlay" onClick={onClose}>
            <div className="cb-attachment-modal" onClick={(e) => e.stopPropagation()}>
                <div className="cb-attachment-modal-header">
                    <span className="cb-attachment-modal-title">All Attachments ({attachments.length})</span>
                    <button className="cb-attachment-modal-close" onClick={onClose}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                </div>
                <div className="cb-attachment-modal-content">
                    {attachments.map(att => (
                        <div key={att.id} className="cb-attachment-modal-item">
                            <AttachmentItem att={att} />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

const AttachmentList: React.FC<{ attachments: Attachment[] }> = ({ attachments }) => {
    const [isModalOpen, setIsModalOpen] = useState(false);
    const visibleAttachments = attachments.slice(0, MAX_VISIBLE_ATTACHMENTS);
    const hiddenCount = attachments.length - MAX_VISIBLE_ATTACHMENTS;

    return (
        <>
            <div className="cb-msg-attachments">
                {visibleAttachments.map(att => (
                    <AttachmentItem key={att.id} att={att} />
                ))}
                {hiddenCount > 0 && (
                    <button
                        className="cb-msg-attachment-more"
                        onClick={() => setIsModalOpen(true)}
                        title={`View all ${attachments.length} attachments`}
                    >
                        <span className="cb-msg-attachment-more-count">+{hiddenCount}</span>
                        <span className="cb-msg-attachment-more-text">more</span>
                    </button>
                )}
            </div>
            <AttachmentModal
                attachments={attachments}
                isOpen={isModalOpen}
                onClose={() => setIsModalOpen(false)}
            />
        </>
    );
};

export const MessageBubble: React.FC<MessageProps> = (props) => {
    const {
        role,
        steps,
        shouldAnimate = true
    } = props;

    // If steps are present, use steps rendering
    if (steps && steps.length > 0) {
        return (
            <div className={`cb-message-row ${role === 'user' ? 'cb-row-user' : 'cb-row-assistant'}`}>
                {role === 'assistant' && (
                    <div className="cb-avatar-container">
                        <div className="cb-avatar-assistant">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 0 1 10 10c0 5.524-4.476 10-10 10S2 17.524 2 12 6.476 2 12 2z" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
                        </div>
                    </div>
                )}

                <div className="cb-message-content-wrapper" style={{ width: '100%' }}>
                    {role === 'user' ? null : <div className="cb-sender-name">Assistant</div>}

                    {role === 'user' && props.attachments && props.attachments.length > 0 && (
                        <AttachmentList attachments={props.attachments} />
                    )}

                    <div className="cb-steps-container">
                        {steps.map((step, index) => {
                            const isLast = index === steps.length - 1;

                            if (step.type === 'thinking') {
                                return (
                                    <ThinkingBlock
                                        key={step.id}
                                        step={step}
                                    />
                                );
                            }

                            if (step.type === 'tool-call') {
                                const toolHandler = step.toolName ? props.onToolCall?.[step.toolName] : undefined;
                                return (
                                    <div key={step.id} className="cb-step-tool" style={{ marginBottom: 8 }}>
                                        <ToolInvocation
                                            toolName={step.toolName || 'Tool'}
                                            args={step.toolArgs}
                                            status={step.toolStatus as any}
                                            result={step.toolResult}
                                            onAction={toolHandler}
                                        />
                                    </div>
                                );
                            }

                            if (step.type === 'confirm-request') {
                                return (
                                    <div key={step.id} className="cb-step-confirm" style={{ marginBottom: 8 }}>
                                        <ConfirmButtons
                                            toolCallId={step.toolCallId || step.id}
                                            toolName={step.toolName || 'Tool'}
                                            label={step.confirmLabel || step.toolName || 'Confirm Action'}
                                            description={step.confirmDescription}
                                            status={step.confirmStatus || 'pending'}
                                            onConfirm={props.onConfirm || (() => { })}
                                            onReject={props.onReject || (() => { })}
                                        />
                                    </div>
                                );
                            }

                            if (step.type === 'text') {
                                return (
                                    <div key={step.id} className={`cb-message-bubble ${role}`} style={{ marginBottom: 8, maxWidth: '100%' }}>
                                        <TypewriterText
                                            content={step.content || ''}
                                            shouldAnimate={shouldAnimate && isLast}
                                            onComplete={isLast ? props.onAnimationComplete : undefined}
                                        />
                                        {isLast && props.isWaitingForDeltas && (!step.content || step.content.trim().length === 0) && (
                                            <BlinkingIndicator />
                                        )}
                                    </div>
                                );
                            }
                            return null;
                        })}
                    </div>
                </div>
            </div>
        );
    }

    // --- LEGACY RENDER PATH (simple content) ---
    return <LegacyMessageBubble {...props} />;
};

// Collapsible Thinking Block
const ThinkingBlock = ({ step }: { step: MessageStep }) => {
    const [isOpen, setIsOpen] = useState(false);
    const isFinished = step.isFinished;
    const hasContent = step.content && step.content.trim().length > 0;

    return (
        <div className="cb-step-thinking-block" style={{ marginBottom: 8, border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, overflow: 'hidden' }}>
            <div
                className="cb-thinking-header"
                onClick={() => setIsOpen(!isOpen)}
                style={{
                    padding: '8px 12px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    cursor: 'pointer',
                    background: 'rgba(255,255,255,0.02)',
                    fontSize: '13px',
                    color: isFinished ? 'rgba(255,255,255,0.6)' : 'rgba(255,255,255,0.9)'
                }}
            >
                {isFinished ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"></polyline></svg>
                ) : (
                    <div className="cb-thinking-spinner" style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: 'white', borderRadius: '50%', animation: 'spin 1s linear infinite' }}></div>
                )}

                <span style={{ flex: 1 }}>Thinking</span>

                {hasContent && (
                    <span style={{ fontSize: '11px', opacity: 0.5 }}>
                        {isOpen ? 'Hide' : 'Show'} content
                    </span>
                )}

                <svg
                    width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                    style={{ transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                >
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>

            {isOpen && hasContent && (
                <div className="cb-thinking-content" style={{ padding: '12px', background: 'rgba(0,0,0,0.2)', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                    <div style={{ 
                        fontSize: '13px', 
                        fontFamily: 'monospace', 
                        color: 'rgba(255,255,255,0.8)', 
                        whiteSpace: 'pre-wrap',
                        lineHeight: '1.5'
                    }}>
                        {step.content}
                    </div>
                </div>
            )}
            <style>{`
                @keyframes spin { to { transform: rotate(360deg); } }
            `}</style>
        </div>
    );
};

// Typewriter with DUAL-TIMER strategy for background resilience
const TypewriterText = ({ content, shouldAnimate, onComplete }: { content: string, shouldAnimate: boolean, onComplete?: () => void }) => {
    const [displayContent, setDisplayContent] = useState(shouldAnimate ? '' : content);
    const hasFiredCompleteRef = useRef(false);

    useEffect(() => {
        // Reset for new content
        hasFiredCompleteRef.current = false;

        if (!shouldAnimate || !content) {
            setDisplayContent(content);
            if (!hasFiredCompleteRef.current) {
                hasFiredCompleteRef.current = true;
                onComplete?.();
            }
            return;
        }

        let animationFrameId: number;
        let timeoutId: any;
        const startTime = Date.now();
        const speed = 15; // ms per char
        const duration = content.length * speed;

        // 1. VISUAL TIMER (requestAnimationFrame) - Smooth, but pauses in background
        const animate = () => {
            const now = Date.now();
            const elapsed = now - startTime;
            const charsToShow = Math.floor(elapsed / speed);

            if (charsToShow < content.length) {
                setDisplayContent(content.substring(0, charsToShow + 1));
                animationFrameId = requestAnimationFrame(animate);
            } else {
                setDisplayContent(content);
                if (!hasFiredCompleteRef.current) {
                    hasFiredCompleteRef.current = true;
                    onComplete?.();
                }
            }
        };

        animationFrameId = requestAnimationFrame(animate);

        // 2. LOGICAL TIMER (setTimeout) - Throttled in background, but GUARANTEED to run
        timeoutId = setTimeout(() => {
            if (!hasFiredCompleteRef.current) {
                setDisplayContent(content);
                hasFiredCompleteRef.current = true;
                onComplete?.();
            }
        }, duration + 100);

        return () => {
            cancelAnimationFrame(animationFrameId);
            clearTimeout(timeoutId);
        };
    }, [content, shouldAnimate]);

    return (
        <div className="cb-markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {displayContent}
            </ReactMarkdown>
            {shouldAnimate && content && displayContent.length < content.length && (
                <span className="cb-cursor">|</span>
            )}
        </div>
    );
};

// Legacy path for simple content without steps
const LegacyMessageBubble: React.FC<MessageProps> = (props) => {
    const {
        role,
        content,
        attachments,
        toolInvocation,
        shouldAnimate = true
    } = props;

    const [displayContent, setDisplayContent] = useState('');
    const hasFiredCompleteRef = useRef(false);

    useEffect(() => {
        hasFiredCompleteRef.current = false;

        if (role !== 'assistant' || !content || !shouldAnimate) {
            setDisplayContent(content || '');
            if (!hasFiredCompleteRef.current) {
                hasFiredCompleteRef.current = true;
                props.onAnimationComplete?.();
            }
            return;
        }

        let animationFrameId: number;
        let timeoutId: any;
        const startTime = Date.now();
        const speed = 15;
        const duration = content.length * speed;

        const animate = () => {
            const now = Date.now();
            const elapsed = now - startTime;
            const charsToShow = Math.floor(elapsed / speed);

            if (charsToShow < content.length) {
                setDisplayContent(content.substring(0, charsToShow + 1));
                animationFrameId = requestAnimationFrame(animate);
            } else {
                setDisplayContent(content);
                if (!hasFiredCompleteRef.current) {
                    hasFiredCompleteRef.current = true;
                    props.onAnimationComplete?.();
                }
            }
        };

        animationFrameId = requestAnimationFrame(animate);

        timeoutId = setTimeout(() => {
            if (!hasFiredCompleteRef.current) {
                setDisplayContent(content);
                hasFiredCompleteRef.current = true;
                props.onAnimationComplete?.();
            }
        }, duration + 100);

        return () => {
            cancelAnimationFrame(animationFrameId);
            clearTimeout(timeoutId);
        };
    }, [content, role, shouldAnimate]);

    if (toolInvocation) {
        return (
            <div className="cb-message-row cb-row-assistant" style={{ marginBottom: '8px' }}>
                <div className="cb-avatar-container">
                    <div className="cb-avatar-assistant" style={{ background: '#333' }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></svg>
                    </div>
                </div>
                <div className="cb-message-content-wrapper">
                    <ToolInvocation
                        toolName={toolInvocation.toolName}
                        args={toolInvocation.args}
                        status={toolInvocation.status}
                        result={toolInvocation.result}
                    />
                </div>
            </div>
        );
    }

    return (
        <div className={`cb-message-row ${role === 'user' ? 'cb-row-user' : 'cb-row-assistant'}`}>
            {role === 'assistant' && (
                <div className="cb-avatar-container">
                    <div className="cb-avatar-assistant">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 0 1 10 10c0 5.524-4.476 10-10 10S2 17.524 2 12 6.476 2 12 2z" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
                    </div>
                </div>
            )}

            <div className="cb-message-content-wrapper">
                {role === 'user' ? null : <div className="cb-sender-name">Assistant</div>}
                <div className={`cb-message-bubble ${role}`}>
                    <div className="cb-markdown-content">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {displayContent}
                        </ReactMarkdown>
                        {role === 'assistant' && shouldAnimate && content && displayContent.length < content.length && (
                            <span className="cb-cursor">|</span>
                        )}
                        {role === 'assistant' && props.isWaitingForDeltas && (!content || content.trim().length === 0) && (
                            <BlinkingIndicator />
                        )}
                    </div>
                    {attachments && attachments.length > 0 && (
                        <AttachmentList attachments={attachments} />
                    )}
                </div>
            </div>
        </div>
    );
};
