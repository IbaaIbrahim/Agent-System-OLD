import React from 'react';
import './BranchNavigator.css';

export interface BranchNavigatorProps {
    branchCount: number;
    activeBranchIndex: number;
    onPrevBranch: () => void;
    onNextBranch: () => void;
}

export const BranchNavigator: React.FC<BranchNavigatorProps> = ({
    branchCount,
    activeBranchIndex,
    onPrevBranch,
    onNextBranch,
}) => {
    if (branchCount <= 1) return null;

    return (
        <div className="cb-branch-nav">
            <button
                className="cb-branch-nav-btn"
                onClick={onPrevBranch}
                disabled={activeBranchIndex <= 0}
                title="Previous version"
            >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <polyline points="15 18 9 12 15 6" />
                </svg>
            </button>
            <span className="cb-branch-nav-label">
                {activeBranchIndex + 1} / {branchCount}
            </span>
            <button
                className="cb-branch-nav-btn"
                onClick={onNextBranch}
                disabled={activeBranchIndex >= branchCount - 1}
                title="Next version"
            >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <polyline points="9 18 15 12 9 6" />
                </svg>
            </button>
        </div>
    );
};
