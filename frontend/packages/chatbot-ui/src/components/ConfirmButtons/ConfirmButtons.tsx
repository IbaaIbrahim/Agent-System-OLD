import React from 'react';
import './ConfirmButtons.css';

export type ConfirmStatus = 'pending' | 'confirmed' | 'rejected' | 'executing';

export interface ConfirmButtonsProps {
    toolCallId: string;
    toolName: string;
    label: string;
    description?: string;
    status: ConfirmStatus;
    onConfirm: (toolCallId: string) => void;
    onReject: (toolCallId: string) => void;
}

export const ConfirmButtons: React.FC<ConfirmButtonsProps> = ({
    toolCallId,
    toolName,
    label,
    description,
    status,
    onConfirm,
    onReject,
}) => {
    if (status !== 'pending') {
        return (
            <div className="cb-confirm-status">
                {status === 'confirmed' && (
                    <div className="cb-confirm-status-confirmed">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="20 6 9 17 4 12"></polyline>
                        </svg>
                        <span>Confirmed - executing...</span>
                    </div>
                )}
                {status === 'executing' && (
                    <div className="cb-confirm-status-executing">
                        <div className="cb-confirm-spinner"></div>
                        <span>Executing {label}...</span>
                    </div>
                )}
                {status === 'rejected' && (
                    <div className="cb-confirm-status-rejected">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                        <span>Cancelled</span>
                    </div>
                )}
            </div>
        );
    }

    return (
        <div className="cb-confirm-buttons">
            <div className="cb-confirm-header">
                <svg className="cb-tool-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                </svg>
                <span className="cb-tool-label">{label}</span>
            </div>
            {description && <p className="cb-confirm-description">{description}</p>}
            <div className="cb-confirm-actions">
                <button
                    className="cb-btn-confirm"
                    onClick={() => onConfirm(toolCallId)}
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                    Confirm
                </button>
                <button
                    className="cb-btn-reject"
                    onClick={() => onReject(toolCallId)}
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                    </svg>
                    Cancel
                </button>
            </div>
        </div>
    );
};
