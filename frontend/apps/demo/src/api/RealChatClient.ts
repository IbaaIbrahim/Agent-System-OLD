
import { ChatClient, ChatState } from './types';
import { MessageProps, MessageStep } from '@chatbot-ui/core';

const GATEWAY_URL = 'http://localhost:8000/api';

export class RealChatClient implements ChatClient {
    private accessToken: string | null = null;
    private messages: MessageProps[] = [];

    constructor(token: string) {
        this.accessToken = token;
    }

    setToken(token: string) {
        this.accessToken = token;
    }

    async sendMessage(content: string, onUpdate: (state: ChatState) => void): Promise<void> {
        if (!this.accessToken) {
            console.error('No access token available');
            return;
        }

        // 1. Add User Message immediately
        const userMsg: MessageProps = {
            id: Date.now().toString(),
            role: 'user',
            content
        };
        this.messages = [...this.messages, userMsg];
        onUpdate({ messages: this.messages, isThinking: true });

        try {
            // 2. Prepare request to Gateway
            // Mapping internal messages to API format
            const apiMessages = this.messages.map(m => ({
                role: m.role,
                content: m.content
            }));

            // 3. Make the API Call
            const response = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.accessToken}`
                },
                body: JSON.stringify({
                    messages: apiMessages,
                    model: 'gpt-4o', // Defaulting to a model, can be made configurable
                    stream: true
                })
            });

            if (!response.ok) {
                // Try to read error details
                let errorDetails = response.statusText;
                try {
                    const errorJson = await response.json();
                    errorDetails = JSON.stringify(errorJson);
                } catch (e) { /* ignore */ }

                throw new Error(`Gateway Request Failed: ${response.status} - ${errorDetails}`);
            }

            // 4. Handle Streaming Response
            const { stream_url, job_id } = await response.json();

            if (!stream_url) {
                throw new Error('No stream URL received from gateway');
            }

            // Create placeholder assistant message
            const assistantMsgId = (Date.now() + 1).toString();
            const assistantMsg: MessageProps = {
                id: assistantMsgId,
                role: 'assistant',
                content: ''
            };
            this.messages = [...this.messages, assistantMsg];
            onUpdate({ messages: this.messages, isThinking: true });

            // Connect to SSE Stream
            const eventSource = new EventSource(stream_url);
            let fullContent = '';

            eventSource.onmessage = (event) => {
                // Determine if event data is JSON or plain text
                try {
                    const data = JSON.parse(event.data);

                    // Handle different event types if structured, or assume content delta
                    // Setup based on typical SSE delta format: { delta: "text" } or just "text"
                    // Adjust based on actual Stream Edge implementation.
                    // Assuming structure: { content: "token", ... } or similar.
                    // If stream edge sends raw tokens as data, valid JSON might not be guaranteed for partials, 
                    // but usually it sends JSON objects.

                    if (data.content) {
                        fullContent += data.content;
                        // Update the last message
                        this.messages = this.messages.map(m =>
                            m.id === assistantMsgId ? { ...m, content: fullContent } : m
                        );
                        onUpdate({ messages: this.messages, isThinking: true });
                    }

                    if (data.status === 'completed' || data.done) {
                        eventSource.close();
                        onUpdate({ messages: this.messages, isThinking: false });
                    }

                } catch (e) {
                    // If not JSON, maybe raw text?
                    console.warn('SSE Parse Error or limit reached', e);
                }
            };

            eventSource.onerror = (err) => {
                console.error('EventSource failed:', err);
                eventSource.close();
                onUpdate({ messages: this.messages, isThinking: false });

                if (fullContent.length === 0) {
                    // If we failed before getting anything
                    this.messages = this.messages.map(m =>
                        m.id === assistantMsgId ? { ...m, content: 'Error: Connection to stream failed.' } : m
                    );
                    onUpdate({ messages: this.messages, isThinking: false });
                }
            };

            // Listen for specific event types if backend sends named events
            eventSource.addEventListener('completion', () => {
                eventSource.close();
                onUpdate({ messages: this.messages, isThinking: false });
            });


        } catch (error: any) {
            console.error('RealChatClient Error:', error);

            // Add error message as assistant response for visibility
            const errorMsg: MessageProps = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: `Error: ${error.message || 'Unknown error occurred.'}`
            };
            this.messages = [...this.messages, errorMsg];
            onUpdate({ messages: this.messages, isThinking: false });
        }
    }

    reset(onUpdate: (state: ChatState) => void): void {
        this.messages = [];
        onUpdate({ messages: [], isThinking: false });
    }
}
