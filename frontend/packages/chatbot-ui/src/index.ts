import './styles.css';

// UI Components
export * from './components/ChatContainer/ChatContainer';
export * from './components/Composer/Composer';
export * from './components/MessageBubble/MessageBubble';
export * from './components/ToolInvocation/ToolInvocation';
export * from './components/ThinkingIndicator/ThinkingIndicator';
export * from './components/WelcomeScreen/WelcomeScreen';
export * from './components/NavigationSidebar/NavigationSidebar';
export * from './components/ChatLauncher/ChatLauncher';
export * from './components/PendingMessageList/PendingMessageList';
export * from './components/ConfirmButtons/ConfirmButtons';
export * from './components/FileUpload/FileUpload';

// API Client & Types
export { RealChatClient } from './api/RealChatClient';
export { AuthClient } from './api/AuthClient';
export type { TokenResponse } from './api/AuthClient';
export type { ChatState, ChatClient, AttachedFile, ConversationSummary, ConversationDetail, ConversationMessage, ConversationListResponse } from './api/types';

// Live Assistant
export * from './components/LiveAssistant/LiveAssistant';
export { LiveWebSocketClient } from './api/LiveWebSocketClient';
export type { LiveWSConfig, LiveWSMessage, LiveWSMessageType } from './api/LiveWebSocketClient';
export * from './hooks/useLiveSession';
export * from './hooks/useAudioPlayback';
export * from './hooks/useScreenCapture';
export * from './hooks/useVAD';

// High-level Abstractions
export * from './components/Chatbot/Chatbot';
export * from './hooks/useChat';

