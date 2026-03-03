import React from 'react';
const { useState, useRef, useEffect, useCallback } = React;
import './Composer.css';
import { ReasoningMenu, PlusMenu, EffortLevel } from '../ComposerMenu/ComposerMenu';
import { ReplyPreview } from '../ReplyPreview/ReplyPreview';
import { AttachedFile } from '../../api/types';

export interface ReplyingTo {
    id: string;
    role: string;
    content: string;
}

export interface ComposerProps {
    onSend?: (text: string, fileIds?: string[], attachedFiles?: AttachedFile[], replyToMessageId?: string) => void;
    disabled?: boolean;
    placeholder?: string;
    effortLevel?: EffortLevel;
    onEffortLevelChange?: (level: EffortLevel) => void;
    webSearchEnabled?: boolean;
    onWebSearchChange?: (enabled: boolean) => void;
    pageContextEnabled?: boolean;
    onPageContextChange?: (enabled: boolean) => void;
    onFileUpload?: (file: File) => Promise<AttachedFile>;
    replyingTo?: ReplyingTo | null;
    onDismissReply?: () => void;
}

const ALLOWED_TYPES = [
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/gif',
    'image/webp',
    'application/pdf',
    'text/plain',
    'text/markdown',
    'text/csv',
];

const MAX_FILE_SIZE = 25 * 1024 * 1024; // 25MB

const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const getFileIcon = (contentType: string): string => {
    if (contentType.startsWith('image/')) return '🖼️';
    if (contentType === 'application/pdf') return '📄';
    if (contentType.startsWith('text/')) return '📝';
    return '📎';
};

export const Composer: React.FC<ComposerProps> = ({
    onSend,
    disabled = false,
    placeholder = "Ask, @mention, or / for actions",
    effortLevel = 'medium',
    onEffortLevelChange,
    webSearchEnabled = false,
    onWebSearchChange,
    pageContextEnabled = false,
    onPageContextChange,
    onFileUpload,
    replyingTo,
    onDismissReply
}) => {
    const [input, setInput] = useState('');
    const [activeMenu, setActiveMenu] = useState<'plus' | 'reasoning' | null>(null);
    const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadError, setUploadError] = useState<string | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const composerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (composerRef.current && !composerRef.current.contains(event.target as Node)) {
                setActiveMenu(null);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleSendMessage = useCallback(() => {
        if ((input.trim() || attachedFiles.length > 0) && !disabled) {
            const fileIds = attachedFiles.length > 0 ? attachedFiles.map(f => f.file_id) : undefined;
            onSend?.(input, fileIds, attachedFiles.length > 0 ? [...attachedFiles] : undefined, replyingTo?.id);
            setInput('');
            setAttachedFiles([]);
            setUploadError(null);
            onDismissReply?.();
        }
    }, [input, attachedFiles, disabled, onSend, replyingTo, onDismissReply]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    };

    const toggleMenu = (menu: 'plus' | 'reasoning') => {
        setActiveMenu(activeMenu === menu ? null : menu);
    };

    const processFile = useCallback(async (file: File) => {
        console.log('[Composer] processFile called', { name: file.name, type: file.type, size: file.size, hasOnFileUpload: !!onFileUpload });
        if (!onFileUpload) {
            console.warn('[Composer] onFileUpload is not available, skipping upload');
            return;
        }

        // Validate type
        if (!ALLOWED_TYPES.includes(file.type)) {
            console.warn('[Composer] Unsupported file type:', file.type);
            setUploadError(`Unsupported file type: ${file.type}`);
            return;
        }

        // Validate size
        if (file.size > MAX_FILE_SIZE) {
            setUploadError(`File too large: ${formatFileSize(file.size)} (max ${formatFileSize(MAX_FILE_SIZE)})`);
            return;
        }

        setIsUploading(true);
        setUploadError(null);

        try {
            console.log('[Composer] Uploading file...');
            const result = await onFileUpload(file);
            console.log('[Composer] Upload result:', result);
            // Create a local blob URL so the image can display immediately without a server round-trip
            result.localBlobUrl = URL.createObjectURL(file);
            setAttachedFiles(prev => [...prev, result]);
        } catch (err: any) {
            console.error('[Composer] Upload failed:', err);
            setUploadError(err.message || 'Upload failed');
        } finally {
            setIsUploading(false);
        }
    }, [onFileUpload]);

    const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
        console.log('[Composer] handleFileSelect triggered', e.target.files);
        const files = e.target.files;
        if (files) {
            for (let i = 0; i < files.length; i++) {
                await processFile(files[i]);
            }
        }
        // Clear so the same file can be re-selected
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    }, [processFile]);

    const removeAttachedFile = (fileId: string) => {
        setAttachedFiles(prev => prev.filter(f => f.file_id !== fileId));
    };

    const handleUploadClick = () => {
        console.log('[Composer] handleUploadClick, fileInputRef:', !!fileInputRef.current);
        setActiveMenu(null);
        fileInputRef.current?.click();
    };

    // Drag and drop handlers
    const handleDragEnter = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.dataTransfer.types.includes('Files')) {
            setIsDragging(true);
        }
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        // Only set false if leaving the composer entirely
        const rect = composerRef.current?.getBoundingClientRect();
        if (rect) {
            const { clientX, clientY } = e;
            if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
                setIsDragging(false);
            }
        }
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
    };

    const handleDrop = async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        const files = Array.from(e.dataTransfer.files);
        for (const file of files) {
            await processFile(file);
        }
    };

    const hasContent = input.trim() || attachedFiles.length > 0;

    return (
        <div
            className={`cb-composer ${isDragging ? 'cb-composer-drag-active' : ''}`}
            ref={composerRef}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
        >
            {/* Hidden file input */}
            <input
                ref={fileInputRef}
                type="file"
                onChange={handleFileSelect}
                accept={ALLOWED_TYPES.join(',')}
                style={{ display: 'none' }}
                multiple
            />

            {activeMenu === 'reasoning' && (
                <ReasoningMenu
                    onClose={() => setActiveMenu(null)}
                    effortLevel={effortLevel}
                    onEffortLevelChange={onEffortLevelChange}
                    webSearchEnabled={webSearchEnabled}
                    onWebSearchChange={onWebSearchChange}
                    pageContextEnabled={pageContextEnabled}
                    onPageContextChange={onPageContextChange}
                />
            )}
            {activeMenu === 'plus' && (
                <PlusMenu
                    onClose={() => setActiveMenu(null)}
                    onUploadFile={handleUploadClick}
                />
            )}

            {/* Drag overlay */}
            {isDragging && (
                <div className="cb-drag-overlay">
                    <div className="cb-drag-overlay-content">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                            <polyline points="17 8 12 3 7 8" />
                            <line x1="12" y1="3" x2="12" y2="15" />
                        </svg>
                        <span>Drop files here</span>
                    </div>
                </div>
            )}

            <div className="cb-composer-input-wrapper">
                {/* Reply preview */}
                {replyingTo && onDismissReply && (
                    <ReplyPreview
                        role={replyingTo.role}
                        content={replyingTo.content}
                        onDismiss={onDismissReply}
                    />
                )}

                {/* Attached files preview */}
                {attachedFiles.length > 0 && (
                    <div className="cb-attached-files">
                        {attachedFiles.map(file => (
                            <div key={file.file_id} className="cb-attached-file-chip">
                                <span className="cb-chip-icon">{getFileIcon(file.content_type)}</span>
                                <span className="cb-chip-name" title={file.filename}>
                                    {file.filename}
                                </span>
                                <span className="cb-chip-size">{formatFileSize(file.size_bytes)}</span>
                                <button
                                    className="cb-chip-remove"
                                    onClick={() => removeAttachedFile(file.file_id)}
                                    title="Remove"
                                >
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <line x1="18" y1="6" x2="6" y2="18" />
                                        <line x1="6" y1="6" x2="18" y2="18" />
                                    </svg>
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                {/* Upload progress */}
                {isUploading && (
                    <div className="cb-upload-progress-bar">
                        <div className="cb-upload-progress-text">
                            <span className="cb-upload-spinner-inline"></span>
                            Uploading...
                        </div>
                    </div>
                )}

                {/* Upload error */}
                {uploadError && (
                    <div className="cb-upload-error">
                        <span>⚠️ {uploadError}</span>
                        <button className="cb-error-dismiss" onClick={() => setUploadError(null)}>✕</button>
                    </div>
                )}

                <textarea
                    className="cb-composer-textarea"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    onFocus={() => setActiveMenu(null)}
                    placeholder={attachedFiles.length > 0 ? "Add a message about your file(s)..." : placeholder}
                    disabled={disabled}
                    rows={1}
                />
                <div className="cb-composer-actions">
                    <div className="cb-actions-left">
                        {/* Main Plus Button */}
                        {/* <button
                            className={`cb-action-btn circle ${activeMenu === 'plus' ? 'active-state' : ''}`}
                            onClick={() => toggleMenu('plus')}
                            title="Add..."
                        >
                            {activeMenu === 'plus' ? (
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                            ) : (
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
                            )}
                        </button> */}

                        {/* Attach file shortcut button */}
                        {onFileUpload && (
                            <button
                                className="cb-action-btn"
                                onClick={handleUploadClick}
                                title="Attach file"
                                disabled={isUploading}
                            >
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                                </svg>
                            </button>
                        )}

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
                        className={`cb-send-btn ${hasContent ? 'active' : ''}`}
                        onClick={handleSendMessage}
                        disabled={!hasContent || disabled}
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
