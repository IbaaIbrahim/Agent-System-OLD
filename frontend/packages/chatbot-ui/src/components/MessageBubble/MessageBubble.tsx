import React from 'react';
const { useState, useEffect, useRef, useCallback } = React;
import './MessageBubble.css';
import { ToolInvocation } from '../ToolInvocation/ToolInvocation';
import { ConfirmButtons, ConfirmStatus } from '../ConfirmButtons/ConfirmButtons';
import { AuthenticatedImage } from '../AuthenticatedImage/AuthenticatedImage';
import { BlinkingIndicator } from '../BlinkingIndicator/BlinkingIndicator';
import { MessageActions } from '../MessageActions/MessageActions';
import { BranchNavigator } from '../BranchNavigator/BranchNavigator';
import { useChatbotContext } from '../../context/ChatbotContext';
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
    // Message actions
    onReply?: (message: MessageProps, selectedText?: string) => void;
    onEdit?: (messageId: string, content: string) => void;
    onSwitchBranch?: (branchPointMessageId: string, targetChildMessageId: string) => void;
    // Reply display
    replyToMessageId?: string;
    replyToContent?: string;
    replyToRole?: string;
    // Branching
    parentMessageId?: string;
    branchPoint?: boolean;
    branchCount?: number;
    activeBranchIndex?: number;
    branchIds?: string[];
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

// --- Image Lightbox ---
const ImageLightbox: React.FC<{
    att: Attachment;
    onClose: () => void;
}> = ({ att, onClose }) => {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    return (
        <div className="cb-lightbox-overlay" onClick={onClose}>
            <div className="cb-lightbox-inner" onClick={(e) => e.stopPropagation()}>
                <button className="cb-lightbox-close" onClick={onClose} title="Close (Esc)">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                </button>
                {att.name && <div className="cb-lightbox-filename">{att.name}</div>}
                {att.localUrl ? (
                    <img className="cb-lightbox-img" src={att.localUrl} alt={att.name || 'Image'} />
                ) : (
                    <AuthenticatedImage className="cb-lightbox-img" src={att.url} alt={att.name || 'Image'} />
                )}
                {att.size != null && (
                    <div className="cb-lightbox-meta">{formatFileSize(att.size)}</div>
                )}
            </div>
        </div>
    );
};

// --- Attachment Chip (Claude-style compact chip above message) ---
const AttachmentChip: React.FC<{ att: Attachment }> = ({ att }) => {
    const [lightboxOpen, setLightboxOpen] = useState(false);
    const [fileLoading, setFileLoading] = useState(false);
    const [fileError, setFileError] = useState<string | null>(null);
    const context = useChatbotContext();

    const handleClick = async () => {
        if (att.type === 'image') {
            setLightboxOpen(true);
            return;
        }
        // File: always use openable download URL (never blob/localUrl) so link works in new tab and after reload
        if (!context) {
            window.open(att.url, '_blank', 'noopener,noreferrer');
            return;
        }
        setFileError(null);
        setFileLoading(true);
        try {
            const fileId = att.id || att.url;
            const openableUrl = await context.getFileDownloadUrl(fileId);
            window.open(openableUrl, '_blank', 'noopener,noreferrer');
        } catch (e) {
            setFileError(e instanceof Error ? e.message : 'Failed to open file');
        } finally {
            setFileLoading(false);
        }
    };

    return (
        <>
            <div
                className={`cb-att-chip ${fileLoading ? 'cb-att-chip-loading' : ''} ${fileError ? 'cb-att-chip-error' : ''}`}
                onClick={handleClick}
                title={fileError || att.name}
                role="button"
                aria-busy={fileLoading}
            >
                {att.type === 'image' ? (
                    <div className="cb-att-chip-thumb">
                        {att.localUrl ? (
                            <img src={att.localUrl} alt={att.name || ''} />
                        ) : (
                            <AuthenticatedImage src={att.url} alt={att.name || ''} />
                        )}
                    </div>
                ) : (
                    <div className="cb-att-chip-icon">
                        {fileLoading ? (
                            <span className="cb-att-chip-spinner" aria-hidden />
                        ) : (
                            getFileIcon(att.contentType)
                        )}
                    </div>
                )}
                <div className="cb-att-chip-info">
                    <span className="cb-att-chip-name">{att.name || 'File'}</span>
                    {att.size != null && !fileLoading && (
                        <span className="cb-att-chip-size">{formatFileSize(att.size)}</span>
                    )}
                    {fileError && (
                        <span className="cb-att-chip-error-msg">{fileError}</span>
                    )}
                </div>
                <div className="cb-att-chip-action">
                    {att.type === 'image' ? (
                        // Eye icon for images
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                            <circle cx="12" cy="12" r="3" />
                        </svg>
                    ) : (
                        // External link icon for files
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                            <polyline points="15 3 21 3 21 9" />
                            <line x1="10" y1="14" x2="21" y2="3" />
                        </svg>
                    )}
                </div>
            </div>
            {lightboxOpen && (
                <ImageLightbox att={att} onClose={() => setLightboxOpen(false)} />
            )}
        </>
    );
};

// --- Attachment List (row of chips above message content) ---
const AttachmentList: React.FC<{ attachments: Attachment[] }> = ({ attachments }) => (
    <div className="cb-att-list">
        {attachments.map(att => (
            <AttachmentChip key={att.id} att={att} />
        ))}
    </div>
);

export const MessageBubble: React.FC<MessageProps> = (props) => {
    const { role, steps, shouldAnimate = true } = props;

    const [isEditing, setIsEditing] = useState(false);
    const [editText, setEditText] = useState('');

    const getStepsTextContent = (): string => {
        if (!steps || steps.length === 0) return props.content || '';
        const fromSteps = steps
            .filter(s => s.type === 'text')
            .map(s => s.content || '')
            .join('\n\n');
        return fromSteps || props.content || '';
    };

    const handleStartEdit = useCallback((messageId: string, content: string) => {
        setEditText(content);
        setIsEditing(true);
    }, []);

    const handleCancelEdit = useCallback(() => {
        setIsEditing(false);
        setEditText('');
    }, []);

    const handleSaveEdit = useCallback(() => {
        if (editText.trim() && props.onEdit) {
            props.onEdit(props.id, editText.trim());
            setIsEditing(false);
            setEditText('');
        }
    }, [editText, props.onEdit, props.id]);

    const handleEditKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSaveEdit();
        }
        if (e.key === 'Escape') {
            handleCancelEdit();
        }
    }, [handleSaveEdit, handleCancelEdit]);

    const handlePrevBranch = useCallback(() => {
        if (props.onSwitchBranch && props.branchIds && props.activeBranchIndex != null && props.activeBranchIndex > 0) {
            const parentId = props.parentMessageId;
            if (parentId) {
                props.onSwitchBranch(parentId, props.branchIds[props.activeBranchIndex - 1]);
            }
        }
    }, [props.onSwitchBranch, props.branchIds, props.activeBranchIndex, props.parentMessageId]);

    const handleNextBranch = useCallback(() => {
        if (props.onSwitchBranch && props.branchIds && props.activeBranchIndex != null && props.activeBranchIndex < (props.branchCount || 0) - 1) {
            const parentId = props.parentMessageId;
            if (parentId) {
                props.onSwitchBranch(parentId, props.branchIds[props.activeBranchIndex + 1]);
            }
        }
    }, [props.onSwitchBranch, props.branchIds, props.activeBranchIndex, props.branchCount, props.parentMessageId]);

    const bubbleRef = useRef<HTMLDivElement>(null);
    const [selectionPopup, setSelectionPopup] = useState<{ text: string; top: number; left: number } | null>(null);

    useEffect(() => {
        const handleMouseUp = () => {
            const selection = window.getSelection();
            if (!selection || !selection.toString().trim()) {
                setSelectionPopup(null);
                return;
            }
            const el = bubbleRef.current;
            if (!el || !selection.anchorNode || !el.contains(selection.anchorNode)) {
                return;
            }
            const range = selection.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            const bubbleRect = el.getBoundingClientRect();
            setSelectionPopup({
                text: selection.toString().trim(),
                top: rect.top - bubbleRect.top - 36,
                left: rect.left - bubbleRect.left + rect.width / 2,
            });
        };

        const handleMouseDown = (e: MouseEvent) => {
            if (selectionPopup && !(e.target as HTMLElement)?.closest?.('.cb-selection-reply-popup')) {
                setSelectionPopup(null);
            }
        };

        document.addEventListener('mouseup', handleMouseUp);
        document.addEventListener('mousedown', handleMouseDown);
        return () => {
            document.removeEventListener('mouseup', handleMouseUp);
            document.removeEventListener('mousedown', handleMouseDown);
        };
    }, [selectionPopup]);

    // If steps are present, use steps rendering
    if (steps && steps.length > 0) {
        const textContent = getStepsTextContent();

        return (
            <div ref={bubbleRef} className={`cb-message-row ${role === 'user' ? 'cb-row-user' : 'cb-row-assistant'}`}>
                {role === 'assistant' && (
                    <div className="cb-avatar-container">
                        <div className="cb-avatar-assistant">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 0 1 10 10c0 5.524-4.476 10-10 10S2 17.524 2 12 6.476 2 12 2z" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
                        </div>
                    </div>
                )}

                <div className="cb-message-content-wrapper" style={{ width: '100%', position: 'relative' }}>
                    {role === 'user' ? null : <div className="cb-sender-name">Assistant</div>}

                    {props.branchPoint && props.branchCount && props.branchCount > 1 && (
                        <BranchNavigator
                            branchCount={props.branchCount}
                            activeBranchIndex={props.activeBranchIndex ?? 0}
                            onPrevBranch={handlePrevBranch}
                            onNextBranch={handleNextBranch}
                        />
                    )}

                    {props.replyToContent && (
                        <div className="cb-reply-indicator">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <polyline points="9 17 4 12 9 7" />
                                <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                            </svg>
                            <span className="cb-reply-indicator-text">
                                {props.replyToContent.length > 80
                                    ? props.replyToContent.slice(0, 80) + '...'
                                    : props.replyToContent}
                            </span>
                        </div>
                    )}

                    {role === 'user' && props.attachments && props.attachments.length > 0 && (
                        <AttachmentList attachments={props.attachments} />
                    )}

                    {isEditing && role === 'user' ? (
                        <div className="cb-edit-container">
                            <textarea
                                className="cb-edit-textarea"
                                value={editText}
                                onChange={(e) => setEditText(e.target.value)}
                                onKeyDown={handleEditKeyDown}
                                autoFocus
                                rows={3}
                            />
                            <div className="cb-edit-actions">
                                <button className="cb-edit-cancel" onClick={handleCancelEdit}>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                    Cancel
                                </button>
                                <button className="cb-edit-save" onClick={handleSaveEdit} disabled={!editText.trim()}>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
                                    Send
                                </button>
                            </div>
                        </div>
                    ) : (
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
                    )}

                    {textContent && !isEditing && (
                        <MessageActions
                            content={textContent}
                            role={role as 'user' | 'assistant'}
                            messageId={props.id}
                            onReply={props.onReply ? () => props.onReply!(props) : undefined}
                            onEdit={props.onEdit && role === 'user'
                                ? () => handleStartEdit(props.id, textContent)
                                : undefined}
                        />
                    )}
                </div>

                {selectionPopup && props.onReply && (
                    <div
                        className="cb-selection-reply-popup"
                        style={{ top: selectionPopup.top, left: selectionPopup.left }}
                        onMouseDown={(e) => e.stopPropagation()}
                        onClick={(e) => {
                            e.stopPropagation();
                            props.onReply!(props, selectionPopup.text);
                            setSelectionPopup(null);
                            window.getSelection()?.removeAllRanges();
                        }}
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="9 17 4 12 9 7" />
                            <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                        </svg>
                        Reply
                    </div>
                )}
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
    const { role, content, attachments, toolInvocation, shouldAnimate = true } = props;

    const [displayContent, setDisplayContent] = useState('');
    const [isEditing, setIsEditing] = useState(false);
    const [editText, setEditText] = useState('');
    const hasFiredCompleteRef = useRef(false);
    const legacyBubbleRef = useRef<HTMLDivElement>(null);
    const [selectionPopup, setSelectionPopup] = useState<{ text: string; top: number; left: number } | null>(null);

    useEffect(() => {
        const handleMouseUp = () => {
            const selection = window.getSelection();
            if (!selection || !selection.toString().trim()) {
                setSelectionPopup(null);
                return;
            }
            const el = legacyBubbleRef.current;
            if (!el || !selection.anchorNode || !el.contains(selection.anchorNode)) {
                return;
            }
            const range = selection.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            const bubbleRect = el.getBoundingClientRect();
            setSelectionPopup({
                text: selection.toString().trim(),
                top: rect.top - bubbleRect.top - 36,
                left: rect.left - bubbleRect.left + rect.width / 2,
            });
        };

        const handleMouseDown = (e: MouseEvent) => {
            if (selectionPopup && !(e.target as HTMLElement)?.closest?.('.cb-selection-reply-popup')) {
                setSelectionPopup(null);
            }
        };

        document.addEventListener('mouseup', handleMouseUp);
        document.addEventListener('mousedown', handleMouseDown);
        return () => {
            document.removeEventListener('mouseup', handleMouseUp);
            document.removeEventListener('mousedown', handleMouseDown);
        };
    }, [selectionPopup]);

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

    const handleStartEdit = useCallback(() => {
        setEditText(content || '');
        setIsEditing(true);
    }, [content]);

    const handleCancelEdit = useCallback(() => {
        setIsEditing(false);
        setEditText('');
    }, []);

    const handleSaveEdit = useCallback(() => {
        if (editText.trim() && props.onEdit) {
            props.onEdit(props.id, editText.trim());
            setIsEditing(false);
            setEditText('');
        }
    }, [editText, props.onEdit, props.id]);

    const handleEditKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSaveEdit();
        }
        if (e.key === 'Escape') {
            handleCancelEdit();
        }
    }, [handleSaveEdit, handleCancelEdit]);

    const handlePrevBranch = useCallback(() => {
        if (props.onSwitchBranch && props.branchIds && props.activeBranchIndex != null && props.activeBranchIndex > 0) {
            const parentId = props.parentMessageId;
            if (parentId) {
                props.onSwitchBranch(parentId, props.branchIds[props.activeBranchIndex - 1]);
            }
        }
    }, [props.onSwitchBranch, props.branchIds, props.activeBranchIndex, props.parentMessageId]);

    const handleNextBranch = useCallback(() => {
        if (props.onSwitchBranch && props.branchIds && props.activeBranchIndex != null && props.activeBranchIndex < (props.branchCount || 0) - 1) {
            const parentId = props.parentMessageId;
            if (parentId) {
                props.onSwitchBranch(parentId, props.branchIds[props.activeBranchIndex + 1]);
            }
        }
    }, [props.onSwitchBranch, props.branchIds, props.activeBranchIndex, props.branchCount, props.parentMessageId]);

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
        <div ref={legacyBubbleRef} className={`cb-message-row ${role === 'user' ? 'cb-row-user' : 'cb-row-assistant'}`}>
            {role === 'assistant' && (
                <div className="cb-avatar-container">
                    <div className="cb-avatar-assistant">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2a10 10 0 0 1 10 10c0 5.524-4.476 10-10 10S2 17.524 2 12 6.476 2 12 2z" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
                    </div>
                </div>
            )}

            <div className="cb-message-content-wrapper" style={{ position: 'relative' }}>
                {role === 'user' ? null : <div className="cb-sender-name">Assistant</div>}

                {props.branchPoint && props.branchCount && props.branchCount > 1 && (
                    <BranchNavigator
                        branchCount={props.branchCount}
                        activeBranchIndex={props.activeBranchIndex ?? 0}
                        onPrevBranch={handlePrevBranch}
                        onNextBranch={handleNextBranch}
                    />
                )}

                {props.replyToContent && (
                    <div className="cb-reply-indicator">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="9 17 4 12 9 7" />
                            <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                        </svg>
                        <span className="cb-reply-indicator-text">
                            {props.replyToContent.length > 80
                                ? props.replyToContent.slice(0, 80) + '...'
                                : props.replyToContent}
                        </span>
                    </div>
                )}

                {attachments && attachments.length > 0 && (
                    <AttachmentList attachments={attachments} />
                )}

                {isEditing && role === 'user' ? (
                    <div className="cb-edit-container">
                        <textarea
                            className="cb-edit-textarea"
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            onKeyDown={handleEditKeyDown}
                            autoFocus
                            rows={3}
                        />
                        <div className="cb-edit-actions">
                            <button className="cb-edit-cancel" onClick={handleCancelEdit}>
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                Cancel
                            </button>
                            <button className="cb-edit-save" onClick={handleSaveEdit} disabled={!editText.trim()}>
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
                                Send
                            </button>
                        </div>
                    </div>
                ) : (
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
                    </div>
                )}

                {content && !isEditing && (
                    <MessageActions
                        content={content}
                        role={role as 'user' | 'assistant'}
                        messageId={props.id}
                        onReply={props.onReply ? () => props.onReply!(props) : undefined}
                        onEdit={props.onEdit && role === 'user'
                            ? handleStartEdit
                            : undefined}
                    />
                )}
            </div>

            {selectionPopup && props.onReply && (
                <div
                    className="cb-selection-reply-popup"
                    style={{ top: selectionPopup.top, left: selectionPopup.left }}
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                        e.stopPropagation();
                        props.onReply!(props, selectionPopup.text);
                        setSelectionPopup(null);
                        window.getSelection()?.removeAllRanges();
                    }}
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="9 17 4 12 9 7" />
                        <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
                    </svg>
                    Reply
                </div>
            )}
        </div>
    );
};
