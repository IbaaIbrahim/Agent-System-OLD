import React, { useState, useRef, DragEvent, ChangeEvent } from 'react';
import './FileUpload.css';

export interface FileUploadProps {
    /**
     * API base URL for file uploads
     */
    apiBaseUrl: string;

    /**
     * Authentication token
     */
    accessToken: string;

    /**
     * Callback when file is successfully uploaded
     */
    onFileUploaded?: (fileId: string, filename: string, metadata: any) => void;

    /**
     * Callback when upload fails
     */
    onUploadError?: (error: string) => void;

    /**
     * Maximum file size in bytes (default: 25MB)
     */
    maxFileSize?: number;

    /**
     * Allowed MIME types
     */
    allowedTypes?: string[];
}

interface UploadedFile {
    file_id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    created_at: string;
}

export const FileUpload: React.FC<FileUploadProps> = ({
    apiBaseUrl,
    accessToken,
    onFileUploaded,
    onUploadError,
    maxFileSize = 25 * 1024 * 1024, // 25MB default
    allowedTypes = [
        'image/jpeg',
        'image/jpg',
        'image/png',
        'image/gif',
        'image/webp',
        'application/pdf',
        'text/plain',
        'text/markdown',
        'text/csv',
    ],
}) => {
    const [isDragging, setIsDragging] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleDragEnter = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
    };

    const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
    };

    const handleDrop = async (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0) {
            await uploadFile(files[0]);
        }
    };

    const handleFileSelect = async (e: ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files;
        if (files && files.length > 0) {
            await uploadFile(files[0]);
        }
    };

    const uploadFile = async (file: File) => {
        setError(null);

        // Validate file type
        if (!allowedTypes.includes(file.type)) {
            const errorMsg = `Invalid file type: ${file.type}. Allowed types: ${allowedTypes.join(', ')}`;
            setError(errorMsg);
            onUploadError?.(errorMsg);
            return;
        }

        // Validate file size
        if (file.size > maxFileSize) {
            const errorMsg = `File size (${formatFileSize(file.size)}) exceeds maximum (${formatFileSize(maxFileSize)})`;
            setError(errorMsg);
            onUploadError?.(errorMsg);
            return;
        }

        setIsUploading(true);
        setUploadProgress(0);

        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch(`${apiBaseUrl}/api/v1/files/upload`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Upload failed');
            }

            const result: UploadedFile = await response.json();

            // Add to uploaded files list
            setUploadedFiles((prev) => [result, ...prev]);

            // Notify parent
            onFileUploaded?.(result.file_id, result.filename, result);

            // Clear file input
            if (fileInputRef.current) {
                fileInputRef.current.value = '';
            }
        } catch (err: any) {
            const errorMsg = err.message || 'Upload failed';
            setError(errorMsg);
            onUploadError?.(errorMsg);
        } finally {
            setIsUploading(false);
            setUploadProgress(100);
            setTimeout(() => setUploadProgress(0), 1000);
        }
    };

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

    return (
        <div className="file-upload-container">
            {/* Drop Zone */}
            <div
                className={`file-upload-dropzone ${isDragging ? 'dragging' : ''} ${isUploading ? 'uploading' : ''}`}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onClick={() => !isUploading && fileInputRef.current?.click()}
            >
                <input
                    ref={fileInputRef}
                    type="file"
                    onChange={handleFileSelect}
                    accept={allowedTypes.join(',')}
                    style={{ display: 'none' }}
                    disabled={isUploading}
                />

                {isUploading ? (
                    <div className="upload-progress">
                        <div className="upload-spinner">⏳</div>
                        <div className="upload-text">Uploading...</div>
                    </div>
                ) : (
                    <>
                        <div className="upload-icon">📤</div>
                        <div className="upload-text">
                            {isDragging ? 'Drop file here' : 'Click or drag to upload'}
                        </div>
                        <div className="upload-hint">
                            Images (10MB), PDFs (25MB), Text files (5MB)
                        </div>
                    </>
                )}
            </div>

            {/* Error Message */}
            {error && (
                <div className="file-upload-error">
                    <span className="error-icon">⚠️</span>
                    {error}
                </div>
            )}

            {/* Uploaded Files List */}
            {uploadedFiles.length > 0 && (
                <div className="uploaded-files-list">
                    <div className="list-header">Uploaded Files (15min expiry)</div>
                    {uploadedFiles.map((file) => (
                        <div key={file.file_id} className="uploaded-file-item">
                            <span className="file-icon">{getFileIcon(file.content_type)}</span>
                            <div className="file-info">
                                <div className="file-name">{file.filename}</div>
                                <div className="file-meta">
                                    {formatFileSize(file.size_bytes)} • {file.content_type}
                                </div>
                            </div>
                            <div className="file-id" title="File ID (for chat)">
                                ID: {file.file_id.substring(0, 8)}...
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
