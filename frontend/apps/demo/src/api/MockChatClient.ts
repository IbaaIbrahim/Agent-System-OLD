import { ChatClient, ChatState } from './types';
import { MessageProps } from '@chatbot-ui/core';

export class MockChatClient implements ChatClient {
    private messages: MessageProps[] = [];

    async sendMessage(content: string, onUpdate: (state: ChatState) => void): Promise<void> {
        // 1. Add User Message
        const userMsg: MessageProps = {
            id: Date.now().toString(),
            role: 'user',
            content
        };
        this.messages = [...this.messages, userMsg];
        onUpdate({ messages: this.messages, isThinking: true });

        // 2. Simulate Delay (Thinking)
        await new Promise(resolve => setTimeout(resolve, 1500));

        // 3. Stop Thinking, Start Tool
        onUpdate({ messages: this.messages, isThinking: false });

        // 4. Add Tool Invocation
        const toolMsgId = Date.now().toString();
        const toolMsg: MessageProps = {
            id: toolMsgId,
            role: 'assistant',
            content: '',
            toolInvocation: {
                toolName: 'search_web',
                args: { query: 'relevant info' },
                status: 'running'
            }
        };
        this.messages = [...this.messages, toolMsg];
        onUpdate({ messages: this.messages, isThinking: false });

        // 5. Simulate Tool Execution
        await new Promise(resolve => setTimeout(resolve, 2000));

        // 6. Complete Tool
        this.messages = this.messages.map(m =>
            m.id === toolMsgId
                ? { ...m, toolInvocation: { ...m.toolInvocation!, status: 'completed' as const } }
                : m
        );
        onUpdate({ messages: this.messages, isThinking: false });

        // 7. Add Assistant Response
        const aiMsg: MessageProps = {
            id: (Date.now() + 1).toString(),
            role: 'assistant',
            content: 'Based on your request, I found some relevant information regarding your query. The analysis suggests we should proceed with the mock implementation first. Based on your request, I found some relevant information regarding your query. The analysis suggests we should proceed with the mock implementation first.  Based on your request, I found some relevant information regarding your query. The analysis suggests we should proceed with the mock implementation first. Based on your request, I found some relevant information regarding your query. The analysis suggests we should proceed with the mock implementation first.'
        };
        this.messages = [...this.messages, aiMsg];
        onUpdate({ messages: this.messages, isThinking: false });
    }

    reset(onUpdate: (state: ChatState) => void): void {
        this.messages = [];
        onUpdate({ messages: [], isThinking: false });
    }
}
