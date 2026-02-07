import React, { useState } from 'react';
import './ComposerMenu.css';

interface MenuProps {
   onClose: () => void;
}

export interface ReasoningMenuProps extends MenuProps {
   webSearchEnabled?: boolean;
   onWebSearchChange?: (enabled: boolean) => void;
}

export const ReasoningMenu: React.FC<ReasoningMenuProps> = ({
   onClose,
   webSearchEnabled = false,
   onWebSearchChange
}) => {
   const [reasoning, setReasoning] = useState<'rovo' | 'deep' | 'research'>('rovo');
   const [toggles, setToggles] = useState({
      knowledge: true,
      autoApply: true
   });

   const toggle = (key: keyof typeof toggles) => {
      setToggles(prev => ({ ...prev, [key]: !prev[key] }));
   };

   const handleWebToggle = () => {
      onWebSearchChange?.(!webSearchEnabled);
   };

   return (
      <div className="cb-menu-overlay" onClick={onClose}>
         <div className="cb-menu-content reasoning" onClick={e => e.stopPropagation()}>
            <div className="cb-menu-header">
               <span>Reasoning</span>
            </div>
            <div className="cb-menu-section">
               <div className={`cb-menu-item ${reasoning === 'rovo' ? 'active' : ''}`} onClick={() => setReasoning('rovo')}>
                  <div className="cb-menu-icon-circle">
                     <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" /></svg>
                  </div>
                  <div className="cb-menu-info">
                     <span className="cb-menu-title">Let Rovo decide</span>
                     <span className="cb-menu-desc">Rovo picks the best reasoning</span>
                  </div>
                  {reasoning === 'rovo' && <div className="cb-check"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg></div>}
               </div>
               <div className={`cb-menu-item ${reasoning === 'deep' ? 'active' : ''}`} onClick={() => setReasoning('deep')}>
                  <div className="cb-menu-icon-circle">
                     <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                  </div>
                  <div className="cb-menu-info">
                     <span className="cb-menu-title">Think deeper</span>
                     <span className="cb-menu-desc">Longer thinking for robust responses</span>
                  </div>
                  {reasoning === 'deep' && <div className="cb-check"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg></div>}
               </div>
               <div className={`cb-menu-item ${reasoning === 'research' ? 'active' : ''}`} onClick={() => setReasoning('research')}>
                  <div className="cb-menu-icon-circle">
                     <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M16.2 7.8l-2 6.3-6.4 2.1 2-6.3z" /></svg>
                  </div>
                  <div className="cb-menu-info">
                     <span className="cb-menu-title">Deep research</span>
                     <span className="cb-menu-desc">Synthesize insights and create reports</span>
                  </div>
                  {reasoning === 'research' && <div className="cb-check"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg></div>}
               </div>
            </div>

            <div className="cb-menu-header">
               <span>Sources</span>
            </div>
            <div className="cb-menu-section">
               <div className="cb-menu-toggle-item" onClick={handleWebToggle}>
                  <div className="cb-menu-icon-circle">
                     <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></svg>
                  </div>
                  <span>Include web results</span>
                  <div className={`cb-toggle ${webSearchEnabled ? 'on' : 'off'}`}></div>
               </div>
               <div className="cb-menu-toggle-item" onClick={() => toggle('knowledge')}>
                  <div className="cb-menu-icon-circle">
                     <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="2" width="16" height="20" rx="2" ry="2" /><line x1="9" y1="22" x2="9" y2="22" /><line x1="15" y1="22" x2="15" y2="22" /><line x1="9" y1="6" x2="9" y2="6" /><line x1="15" y1="6" x2="15" y2="6" /><line x1="9" y1="10" x2="9" y2="10" /><line x1="15" y1="10" x2="15" y2="10" /><line x1="9" y1="14" x2="9" y2="14" /><line x1="15" y1="14" x2="15" y2="14" /><line x1="9" y1="18" x2="9" y2="18" /></svg>
                  </div>
                  <span>Search company knowledge</span>
                  <div className={`cb-toggle ${toggles.knowledge ? 'on' : 'off'}`}></div>
               </div>
               <div className="cb-menu-toggle-item" onClick={() => toggle('autoApply')}>
                  <div className="cb-menu-icon-circle">
                     <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" /></svg>
                  </div>
                  <span>Auto-apply AI writing changes</span>
                  <div className={`cb-toggle ${toggles.autoApply ? 'on' : 'off'}`}></div>
               </div>
            </div>
         </div>
      </div>
   );
};

export const PlusMenu: React.FC<MenuProps> = ({ onClose }) => {
   return (
      <div className="cb-menu-overlay" onClick={onClose}>
         <div className="cb-menu-content plus-menu" onClick={e => e.stopPropagation()}>
            <div className="cb-menu-section compact">
               <div className="cb-menu-item">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
                  <span className="cb-menu-title">Upload file</span>
               </div>
               <div className="cb-menu-item">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>
                  <span className="cb-menu-title">Add a link</span>
               </div>
               <div className="cb-menu-item">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="8.5" cy="7" r="4" /><polyline points="17 11 19 13 23 9" /></svg>
                  <span className="cb-menu-title">Mention someone</span>
               </div>
               <div className="cb-menu-item">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
                  <span className="cb-menu-title">More formatting</span>
               </div>
            </div>
         </div>
      </div>
   );
};
