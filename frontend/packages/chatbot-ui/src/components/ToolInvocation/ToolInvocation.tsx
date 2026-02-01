import React, { useState, useEffect } from 'react';
import './ToolInvocation.css';

export type ToolStatus = 'running' | 'completed' | 'failed';

export interface ToolInvocationProps {
    toolName: string;
    args?: Record<string, any>;
    status: ToolStatus;
    result?: any;
}

export const ToolInvocation: React.FC<ToolInvocationProps> = ({
    toolName,
    status,
    args
}) => {
    const [expanded, setExpanded] = useState(false);

    return (
        <div className={`cb-tool-invocation ${status}`}>
            <div className="cb-tool-header" onClick={() => setExpanded(!expanded)}>
                <div className="cb-tool-icon">
                    {status === 'running' ? (
                        <div className="cb-spinner-sm" />
                    ) : (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><Polyline points="9 18 15 12 9 6" /></svg>
                    )}
                </div>
                <span className="cb-tool-name">
                    {status === 'running' ? `Calling ` : `Called `}
                    <strong>{toolName}</strong>
                </span>
                <span className="cb-tool-chevron">
                    <svg
                        width="16" height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0)' }}
                    >
                        <path d="M6 9l6 6 6-6" />
                    </svg>
                </span>
            </div>
            {expanded && (
                <div className="cb-tool-details">
                    <div className="cb-code-block">
                        {JSON.stringify(args, null, 2)}
                    </div>
                </div>
            )}
        </div>
    );
};

const Polyline = (props: any) => (
    <polyline points={props.points} />
);
