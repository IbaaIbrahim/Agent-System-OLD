import React, { useState, useRef, useEffect } from 'react';
import './Composer.css';
import { ReasoningMenu, PlusMenu } from '../ComposerMenu/ComposerMenu';

export interface ComposerProps {
    onSend?: (text: string) => void;
    disabled?: boolean;
    placeholder?: string;
    webSearchEnabled?: boolean;
    onWebSearchChange?: (enabled: boolean) => void;
}

export const Composer: React.FC<ComposerProps> = ({
    onSend,
    disabled = false,
    placeholder = "Ask, @mention, or / for actions",
    webSearchEnabled = false,
    onWebSearchChange
}) => {
    const [input, setInput] = useState('');
    const [activeMenu, setActiveMenu] = useState<'plus' | 'reasoning' | null>(null);
    const menuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
                setActiveMenu(null);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (input.trim() && !disabled) {
                onSend?.(input);
                setInput('');
            }
        }
    };

    const toggleMenu = (menu: 'plus' | 'reasoning') => {
        setActiveMenu(activeMenu === menu ? null : menu);
    };

    return (
        <div className="cb-composer" ref={menuRef}>
            {activeMenu === 'reasoning' && (
                <ReasoningMenu
                    onClose={() => setActiveMenu(null)}
                    webSearchEnabled={webSearchEnabled}
                    onWebSearchChange={onWebSearchChange}
                />
            )}
            {activeMenu === 'plus' && <PlusMenu onClose={() => setActiveMenu(null)} />}

            <div className="cb-composer-input-wrapper">
                <textarea
                    className="cb-composer-textarea"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    onFocus={() => setActiveMenu(null)}
                    placeholder={placeholder}
                    disabled={disabled}
                    rows={1}
                />
                <div className="cb-composer-actions">
                    <div className="cb-actions-left">
                        {/* Main Plus Button */}
                        <button
                            className={`cb-action-btn circle ${activeMenu === 'plus' ? 'active-state' : ''}`}
                            onClick={() => toggleMenu('plus')}
                            title="Add..."
                        >
                            {activeMenu === 'plus' ? (
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                            ) : (
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
                            )}
                        </button>

                        {/* Reasoning / Tools Button */}
                        <button
                            className={`cb-action-btn ${activeMenu === 'reasoning' ? 'active-icon' : ''}`}
                            onClick={() => toggleMenu('reasoning')}
                            title="Reasoning"
                        >
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></svg>
                        </button>
                    </div>

                    <button
                        className={`cb-send-btn ${input.trim() ? 'active' : ''}`}
                        onClick={() => {
                            if (input.trim() && !disabled) {
                                onSend?.(input);
                                setInput('');
                            }
                        }}
                        disabled={!input.trim() || disabled}
                    >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="19" x2="12" y2="5" /><polyline points="5 12 12 5 19 12" /></svg>
                    </button>
                </div>
            </div>
            <div className="cb-composer-footer">
                <span>Uses AI. Verify results.</span>
            </div>
        </div>
    );
};
