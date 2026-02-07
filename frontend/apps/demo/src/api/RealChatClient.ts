
import { ChatClient, ChatState } from './types';
import { MessageProps, MessageStep } from '@chatbot-ui/core';

const GATEWAY_URL = 'http://localhost:8000/api';

export class RealChatClient implements ChatClient {
    private accessToken: string | null = null;
    private messages: MessageProps[] = [];
    private currentModel: string | null = null;
    private currentProvider: string | null = null;
    private enabledTools: string[] = [];



    constructor(token: string) {
        this.accessToken = token;
    }

    setToken(token: string) {
        this.accessToken = token;
    }

    setModel(model: string | null) {
        // If 'auto', we send null to let the gateway use its defaults
        if (model === 'auto' || !model) {
            this.currentModel = null;
            this.currentProvider = null;
        } else {
            this.currentModel = model;
            // Basic inference: gpt* -> openai, else anthropic (adjust as needed)
            this.currentProvider = model.startsWith('gpt') ? 'openai' : 'anthropic';
        }
    }

    setEnabledTools(tools: string[]) {
        this.enabledTools = tools;
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
                    model: this.currentModel,
                    provider: this.currentProvider,
                    stream: true,
                    metadata: {
                        enabled_tools: this.enabledTools
                    }
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

            // Listen for 'delta' events for content updates
            eventSource.addEventListener('delta', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.content) {
                        fullContent += data.content;
                        this.messages = this.messages.map(m => {
                            if (m.id === assistantMsgId) {
                                let updatedM = { ...m, content: fullContent };

                                // If we are in "steps mode", we must sync the content to a text step
                                if (m.steps && m.steps.length > 0) {
                                    const steps = [...m.steps];
                                    const lastStep = steps[steps.length - 1];

                                    if (lastStep && lastStep.type === 'text') {
                                        // Update the existing last text step
                                        steps[steps.length - 1] = {
                                            ...lastStep,
                                            content: (lastStep.content || '') + data.content
                                        };
                                    } else {
                                        // Create a new text step
                                        steps.push({
                                            id: `text-${Date.now()}`,
                                            type: 'text',
                                            content: data.content
                                        });
                                    }
                                    updatedM.steps = steps;
                                }
                                return updatedM;
                            }
                            return m;
                        });
                        onUpdate({ messages: this.messages, isThinking: true });
                    }
                } catch (e) {
                    console.warn('Failed to parse delta event data', e);
                }
            });

            // Listen for 'tool_call' events (for informational display)
            eventSource.addEventListener('tool_call', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Tool call received:', data);

                    this.messages = this.messages.map(m => {
                        if (m.id === assistantMsgId) {
                            let steps = m.steps ? [...m.steps] : [];

                            // Transition from simple content to steps:
                            // Move existing top-level content to a text step so it remains visible
                            if (steps.length === 0 && fullContent) {
                                steps.push({
                                    id: `text-pre-${Date.now()}`,
                                    type: 'text',
                                    content: fullContent
                                });
                            }

                            const newStep: MessageStep = {
                                id: data.id || `tool-${Date.now()}`,
                                type: 'tool-call',
                                toolName: data.name || data.tool_name,
                                toolArgs: data.arguments,
                                toolStatus: 'running'
                            };
                            return { ...m, steps: [...steps, newStep] };
                        }
                        return m;
                    });
                    onUpdate({ messages: this.messages, isThinking: true });
                } catch (e) {
                    console.warn('Failed to parse tool_call event data', e);
                }
            });

            // Listen for 'tool_result' events (when tools complete)
            eventSource.addEventListener('tool_result', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Tool result received:', data);

                    this.messages = this.messages.map(m => {
                        if (m.id === assistantMsgId && m.steps) {
                            const steps = m.steps.map(s => {
                                // Match by tool call ID or name if ID is missing
                                if (s.type === 'tool-call' && (s.id === data.tool_call_id || s.toolName === data.tool_name)) {
                                    return {
                                        ...s,
                                        toolStatus: 'completed',
                                        toolResult: data.result
                                    } as MessageStep;
                                }
                                return s;
                            });
                            return { ...m, steps };
                        }
                        return m;
                    });
                    onUpdate({ messages: this.messages, isThinking: true });
                } catch (e) {
                    console.warn('Failed to parse tool_result event data', e);
                }
            });

            // Listen for 'suspended' events (when job waits for tool execution)
            eventSource.addEventListener('suspended', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Job suspended, waiting for tools:', data.pending_tools);
                    // Job is suspended waiting for tool results - keep connection open
                    // Backend will handle tool execution and resume
                } catch (e) {
                    console.warn('Failed to parse suspended event data', e);
                }
            });

            // Listen for 'complete' event to close connection
            eventSource.addEventListener('complete', () => {
                eventSource.close();
                onUpdate({ messages: this.messages, isThinking: false });
            });

            // Handle errors
            eventSource.onerror = (err) => {
                console.error('EventSource failed:', err);
                eventSource.close();

                if (fullContent.length === 0) {
                    this.messages = this.messages.map(m =>
                        m.id === assistantMsgId ? { ...m, content: 'Error: Connection to stream failed.' } : m
                    );
                }
                onUpdate({ messages: this.messages, isThinking: false });
            };



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
