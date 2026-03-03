import React from 'react';
import { AttachedFile, ChatClient, ChatState } from '../api/types';

export interface UseChatOptions {
    client: ChatClient | null;
    onToolCall?: Record<string, (args: any) => Promise<any>>;
}

interface QueuedMessage {
    text: string;
    fileIds?: string[];
    attachedFiles?: AttachedFile[];
    replyToMessageId?: string;
}

export const useChat = ({ client, onToolCall }: UseChatOptions) => {
    const [chatState, setChatState] = React.useState<ChatState>({ messages: [], isThinking: false, isWaitingForDeltas: false });
    const [messageQueue, setMessageQueue] = React.useState<QueuedMessage[]>([]);
    const [isProcessing, setIsProcessing] = React.useState(false);
    const [isTyping, setIsTyping] = React.useState(false);

    // Ref to track which messages have finished their typewriter animation
    const finishedMessageIdsRef = React.useRef<Set<string>>(new Set());
    const prevMsgCountRef = React.useRef(0);

    // Register custom tool handlers
    React.useEffect(() => {
        if (client && onToolCall && 'setToolHandler' in client) {
            Object.entries(onToolCall).forEach(([name, handler]) => {
                (client as any).setToolHandler(name, handler);
            });
        }
    }, [client, onToolCall]);

    // Process message queue logic
    React.useEffect(() => {
        const processQueue = async () => {
            if (!client || messageQueue.length === 0 || chatState.isThinking) return;

            // Don't send next message if assistant is still "typing" (animating)
            const lastMsg = chatState.messages[chatState.messages.length - 1];
            if (lastMsg?.role === 'assistant') {
                const hasContent = lastMsg.content || (lastMsg.steps && lastMsg.steps.length > 0);
                if (hasContent && !finishedMessageIdsRef.current.has(lastMsg.id)) {
                    if (!isTyping) setIsTyping(true);
                    return;
                }
            }

            const nextItem = messageQueue[0];
            setIsProcessing(true);
            setMessageQueue(prev => prev.slice(1));

            try {
                await client.sendMessage(nextItem.text, setChatState, nextItem.fileIds, nextItem.attachedFiles, nextItem.replyToMessageId);
            } finally {
                setIsProcessing(false);
            }
        };

        processQueue();
    }, [messageQueue, isProcessing, isTyping, chatState.isThinking, chatState.messages, client]);

    // Track typewriter starts
    React.useEffect(() => {
        if (chatState.messages.length > prevMsgCountRef.current) {
            const lastMsg = chatState.messages[chatState.messages.length - 1];
            const hasContent = lastMsg.role === 'assistant' && (lastMsg.content || (lastMsg.steps && lastMsg.steps.length > 0));
            if (hasContent) setIsTyping(true);
        }
        prevMsgCountRef.current = chatState.messages.length;
    }, [chatState.messages]);

    const handleSend = (text: string, fileIds?: string[], attachedFiles?: AttachedFile[], replyToMessageId?: string) => {
        setMessageQueue(prev => [...prev, { text, fileIds, attachedFiles, replyToMessageId }]);
    };

    const handleRemoveQueueItem = (index: number) => {
        setMessageQueue(prev => {
            const newQueue = [...prev];
            newQueue.splice(index, 1);
            return newQueue;
        });
    };

    const handleAnimationComplete = (id: string) => {
        finishedMessageIdsRef.current.add(id);
        setIsTyping(false);
    };

    const loadConversation = async (conversationId: string) => {
        if (!client?.loadConversation) return;
        // Clear animation tracking for fresh conversation
        finishedMessageIdsRef.current.clear();
        prevMsgCountRef.current = 0;
        setMessageQueue([]);
        setIsProcessing(false);
        setIsTyping(false);
        // Load from API — use a local state capture to get loaded messages
        let loadedMessages: typeof chatState.messages = [];
        await client.loadConversation(conversationId, (newState) => {
            loadedMessages = newState.messages;
            setChatState(newState);
        });
        // Mark all loaded messages as already finished (no animation)
        // Use loadedMessages from callback since chatState update is async
        loadedMessages.forEach(m => finishedMessageIdsRef.current.add(m.id));
        prevMsgCountRef.current = loadedMessages.length;
    };

    const reset = () => {
        if (client) client.reset(setChatState);
        setMessageQueue([]);
        setIsProcessing(false);
        setIsTyping(false);
        finishedMessageIdsRef.current.clear();
        prevMsgCountRef.current = 0;
    };

    return {
        messages: chatState.messages,
        isThinking: chatState.isThinking || isProcessing,
        isWaitingForDeltas: chatState.isWaitingForDeltas || false,
        isTyping,
        messageQueue: messageQueue.map(m => m.text),
        sendMessage: handleSend,
        removeQueueItem: handleRemoveQueueItem,
        handleAnimationComplete,
        finishedMessageIds: finishedMessageIdsRef.current,
        loadConversation,
        conversationId: client?.getConversationId?.() ?? null,
        reset
    };
};
