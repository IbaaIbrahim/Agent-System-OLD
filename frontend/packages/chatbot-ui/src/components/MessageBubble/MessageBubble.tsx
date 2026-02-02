import React, { useState, useEffect } from 'react';
import './MessageBubble.css';
import { ToolInvocation } from '../ToolInvocation/ToolInvocation';
import { ThinkingIndicator } from '../ThinkingIndicator/ThinkingIndicator'; // Assumed export

export interface Attachment {
    id: string;
    type: 'image' | 'file';
    url: string;
    name?: string;
}

export type MessageStepType = 'text' | 'thinking' | 'tool-call';

export interface MessageStep {
    id: string; // unique within message
    type: MessageStepType;
    content?: string; // for text
    toolName?: string; // for tool
    toolArgs?: any;    // for tool
    toolStatus?: 'running' | 'completed' | 'failed';
    isFinished?: boolean; // animation finished?
}

export interface MessageProps {
    id: string;
    role: 'user' | 'assistant' | 'system';

    // Legacy / Computed single content (for backward compat if needed)
    content?: string;

    // New: Sequence of steps
    steps?: MessageStep[];

    timestamp?: Date;
    attachments?: Attachment[];

    // Legacy single tool usage (can be mapped to a step)
    toolInvocation?: {
        toolName: string;
        args: any;
        status: 'running' | 'completed' | 'failed';
    };
    onAnimationComplete?: () => void;
    shouldAnimate?: boolean;
}

export const MessageBubble: React.FC<MessageProps> = (props) => {
    const {
        id,
        role,
        content,
        steps,
        attachments,
        toolInvocation,
        shouldAnimate = true
    } = props;

    // Use internal state or just render? 
    // If we want Typewriter effect for text steps, we need state for them.
    // For simplicity: We will render Steps. 
    // If 'steps' isn't present, we fall back to legacy rendering (content + single tool).

    if (!steps || steps.length === 0) {
        // --- LEGACY RENDER PATH ---
        return <LegacyMessageBubble {...props} />;
    }

    // --- STEPS RENDER PATH ---
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
                            return (
                                <div key={step.id} className="cb-step-tool" style={{ marginBottom: 8 }}>
                                    <ToolInvocation
                                        toolName={step.toolName || 'Tool'}
                                        args={step.toolArgs}
                                        status={step.toolStatus as any}
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
                                </div>
                            );
                        }
                        return null;
                    })}
                </div>
            </div>
        </div>
    );
};

// New Collapsible Thinking Block
const ThinkingBlock = ({ step }: { step: MessageStep }) => {
    const [isOpen, setIsOpen] = useState(false);
    const isFinished = step.isFinished;

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
                {/* Icon */}
                {isFinished ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"></polyline></svg>
                ) : (
                    <div className="cb-thinking-spinner" style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: 'white', borderRadius: '50%', animation: 'spin 1s linear infinite' }}></div>
                )}

                <span style={{ flex: 1 }}>{step.content || 'Thinking Process'}</span>

                {step.thoughts && step.thoughts.length > 0 && (
                    <span style={{ fontSize: '11px', opacity: 0.5 }}>{step.thoughts.length} logs</span>
                )}

                {/* Chevron */}
                <svg
                    width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                    style={{ transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                >
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>

            {isOpen && step.thoughts && step.thoughts.length > 0 && (
                <div className="cb-thinking-logs" style={{ padding: '8px 12px', background: 'rgba(0,0,0,0.2)', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                    {step.thoughts.map((log, idx) => (
                        <div key={idx} style={{ fontSize: '12px', fontFamily: 'monospace', color: 'rgba(255,255,255,0.5)', marginBottom: 4 }}>
                            {log}
                        </div>
                    ))}
                </div>
            )}
            <style>{`
                @keyframes spin { to { transform: rotate(360deg); } }
            `}</style>
        </div>
    );
};

// Extracted Typewriter for reuse in steps
const TypewriterText = ({ content, shouldAnimate, onComplete }: { content: string, shouldAnimate: boolean, onComplete?: () => void }) => {
    const [displayContent, setDisplayContent] = useState(shouldAnimate ? '' : content);

    useEffect(() => {
        if (!shouldAnimate) {
            setDisplayContent(content);
            onComplete?.();
            return;
        }

        // If content grew, we might need to continue animating
        if (displayContent.length === content.length) return;

        let animationFrameId: number;
        const startTime = Date.now();
        const speed = 15;

        // We need to account for already displayed characters if we are appending? 
        // For simplicity, let's assume content is replaced or we restart animation if content changes significantly?
        // Actually, usually in these systems, content grows. 
        // Let's stick to the previous simple implementation logic.

        const animate = () => {
            // ... logic from original ...
            const now = Date.now();
            const elapsed = now - startTime;
            const charsToShow = Math.floor(elapsed / speed);

            if (charsToShow < content.length) {
                setDisplayContent(content.substring(0, charsToShow + 1));
                animationFrameId = requestAnimationFrame(animate);
            } else {
                setDisplayContent(content);
                onComplete?.();
            }
        };
        animationFrameId = requestAnimationFrame(animate);
        return () => cancelAnimationFrame(animationFrameId);
    }, [content, shouldAnimate]);

    return (
        <div className="cb-markdown-content">
            {displayContent}
            {shouldAnimate && displayContent.length < content.length && (
                <span className="cb-cursor">|</span>
            )}
        </div>
    );
}

// --- LEGACY COMPONENT (Original Logic) ---
const LegacyMessageBubble: React.FC<MessageProps> = (props) => {
    const {
        role,
        content,
        attachments,
        toolInvocation,
        shouldAnimate = true
    } = props;

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
                    <TypewriterText
                        content={content || ''}
                        shouldAnimate={shouldAnimate && role === 'assistant'}
                        onComplete={props.onAnimationComplete}
                    />
                    {attachments && attachments.length > 0 && (
                        <div className="cb-attachments-grid">
                            {/* Attachment logic */}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
