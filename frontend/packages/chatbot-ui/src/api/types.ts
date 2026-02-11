import { MessageProps } from '../components/MessageBubble/MessageBubble';

export interface AttachedFile {
    file_id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
}

export interface ChatState {
    messages: MessageProps[];
    isThinking: boolean;
}

export interface ConversationSummary {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
}

export interface ConversationMessage {
    id: string;
    role: string;
    content: string | null;
    job_id: string;
    created_at: string | null;
}

export interface ConversationDetail {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
    messages: ConversationMessage[];
}

export interface ConversationListResponse {
    conversations: ConversationSummary[];
    total: number;
    offset: number;
    limit: number;
}

export interface ChatClient {
    sendMessage: (content: string, onUpdate: (state: ChatState) => void, fileIds?: string[], attachedFiles?: AttachedFile[]) => Promise<void>;
    reset: (onUpdate: (state: ChatState) => void) => void;
    setModel: (model: string | null) => void;
    setEnabledTools: (tools: string[]) => void;
    setToolHandler: (name: string, handler: (args: any) => Promise<string | any>) => void;
    enableWebSearch: (enabled: boolean) => void;
    enablePageContext: (enabled: boolean) => void;
    sendConfirmResponse?: (toolCallId: string, confirmed: boolean) => Promise<void>;
    setPageReadingCallback?: (callback: (isReading: boolean) => void) => void;
    uploadFile?: (file: File) => Promise<AttachedFile>;

    // Conversation management
    getConversations?: (offset?: number, limit?: number) => Promise<ConversationListResponse>;
    loadConversation?: (id: string, onUpdate: (state: ChatState) => void) => Promise<void>;
    deleteConversation?: (id: string) => Promise<void>;
    searchConversations?: (query: string, offset?: number, limit?: number) => Promise<ConversationListResponse>;
    setConversationId?: (id: string | null) => void;
    getConversationId?: () => string | null;
}
