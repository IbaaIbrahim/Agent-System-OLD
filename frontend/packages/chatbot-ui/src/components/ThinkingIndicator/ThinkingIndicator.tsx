import React from 'react';
import './ThinkingIndicator.css';

export const ThinkingIndicator = () => {
    return (
        <div className="cb-thinking-container">
            <div className="cb-thinking-dots">
                <div className="cb-dot"></div>
                <div className="cb-dot"></div>
                <div className="cb-dot"></div>
            </div>
            <span className="cb-thinking-text">Thinking...</span>
        </div>
    );
};
