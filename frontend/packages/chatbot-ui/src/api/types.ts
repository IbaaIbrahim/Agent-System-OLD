import { MessageProps } from '../components/MessageBubble/MessageBubble';

export interface ChatState {
    messages: MessageProps[];
    isThinking: boolean;
}

export interface ChatClient {
    sendMessage: (content: string, onUpdate: (state: ChatState) => void) => Promise<void>;
    reset: (onUpdate: (state: ChatState) => void) => void;
    setModel: (model: string | null) => void;
    setEnabledTools: (tools: string[]) => void;
    sendConfirmResponse?: (toolCallId: string, confirmed: boolean) => Promise<void>;
    setPageReadingCallback?: (callback: (isReading: boolean) => void) => void;
}
