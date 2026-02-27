import React from 'react';
import './BlinkingIndicator.css';

export const BlinkingIndicator: React.FC = () => {
    return (
        <div className="cb-blinking-indicator">
            <div className="cb-blinking-circle"></div>
        </div>
    );
};
