
import { AttachedFile, ChatClient, ChatState } from './types';
import { MessageProps, MessageStep } from '../components/MessageBubble/MessageBubble';

export class RealChatClient implements ChatClient {
    private accessToken: string | null = null;
    private apiBaseUrl: string = 'http://localhost:8000/api';
    private messages: MessageProps[] = [];
    private currentModel: string | null = null;
    private currentProvider: string | null = null;
    private enabledTools: string[] = [];
    private currentJobId: string | null = null;
    private pageReadingCallback: ((isReading: boolean) => void) | null = null;
    private customHandlers: Map<string, (args: any) => Promise<string | any>> = new Map();
    private libraryTools: Set<string> = new Set();
    private conversationId: string | null = null;
    private effortLevel: 'low' | 'medium' | 'high' = 'medium';

    constructor(token: string, apiBaseUrl?: string) {
        this.accessToken = token;
        if (apiBaseUrl) {
            this.apiBaseUrl = apiBaseUrl;
        }
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

    setToolHandler(name: string, handler: (args: any) => Promise<string | any>) {
        this.customHandlers.set(name, handler);
    }

    enableWebSearch(enabled: boolean) {
        if (enabled) this.libraryTools.add('web_search');
        else this.libraryTools.delete('web_search');
    }

    enablePageContext(enabled: boolean) {
        if (enabled) {
            this.libraryTools.add('read_page_content');
            this.libraryTools.add('read_page_content_advanced');
            this.libraryTools.add('take_screenshot');
            this.libraryTools.add('inspect_dom_element');
        } else {
            this.libraryTools.delete('read_page_content');
            this.libraryTools.delete('read_page_content_advanced');
            this.libraryTools.delete('take_screenshot');
            this.libraryTools.delete('inspect_dom_element');
        }
    }

    enableTakeScreenshot(enabled: boolean) {
        if (enabled) this.libraryTools.add('take_screenshot');
        else this.libraryTools.delete('take_screenshot');
    }

    enableDomInspector(enabled: boolean) {
        if (enabled) this.libraryTools.add('inspect_dom_element');
        else this.libraryTools.delete('inspect_dom_element');
    }

    private getActiveTools(): string[] {
        const all = new Set([...this.enabledTools, ...Array.from(this.libraryTools)]);
        return Array.from(all);
    }

    setEffortLevel(level: 'low' | 'medium' | 'high') {
        this.effortLevel = level;
    }

    setPageReadingCallback(callback: (isReading: boolean) => void) {
        this.pageReadingCallback = callback;
    }


    async sendMessage(content: string, onUpdate: (state: ChatState) => void, fileIds?: string[], attachedFiles?: import('./types').AttachedFile[]): Promise<void> {
        if (!this.accessToken) {
            console.error('No access token available');
            return;
        }

        // 1. Add User Message immediately
        const userMsg: MessageProps = {
            id: Date.now().toString(),
            role: 'user',
            content,
            attachments: attachedFiles?.map(f => ({
                id: f.file_id,
                type: (f.content_type.startsWith('image/') ? 'image' : 'file') as 'image' | 'file',
                url: `${this.apiBaseUrl}/v1/files/${f.file_id}/download`,
                name: f.filename,
                size: f.size_bytes,
                contentType: f.content_type,
                localUrl: f.localBlobUrl,
            }))
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

            // Build metadata with file_ids if present
            const metadata: Record<string, any> = {
                enabled_tools: this.getActiveTools(),
                effort_level: this.effortLevel,
            };
            if (fileIds && fileIds.length > 0) {
                metadata.file_ids = fileIds;
            }

            // 3. Make the API Call
            const response = await fetch(`${this.apiBaseUrl}/v1/chat/completions`, {
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
                    metadata,
                    conversation_id: this.conversationId || undefined,
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
            const { stream_url, job_id, conversation_id } = await response.json();
            this.currentJobId = job_id;
            if (conversation_id) {
                this.conversationId = conversation_id;
            }

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
            let lastDeltaTime = Date.now(); // Initialize to now - will be updated on first delta
            let deltaCheckInterval: ReturnType<typeof setInterval> | null = null;
            let isStreaming = true;

            // Check periodically if we're waiting for deltas (no deltas received in last 2 seconds)
            // Only show indicator if we've been waiting for at least 2 seconds
            const checkDeltaTimeout = () => {
                if (!isStreaming) return;
                const timeSinceLastDelta = Date.now() - lastDeltaTime;
                const isWaitingForDeltas = timeSinceLastDelta > 2000;
                onUpdate({ messages: this.messages, isThinking: true, isWaitingForDeltas });
            };

            // Start checking every 500ms after initial 2 second delay
            setTimeout(() => {
                if (isStreaming) {
                    deltaCheckInterval = setInterval(checkDeltaTimeout, 500);
                }
            }, 2000);

            // Track thinking content separately
            let thinkingContent = '';

            // Listen for 'reasoning_delta' events (thinking/reasoning tokens)
            eventSource.addEventListener('reasoning_delta', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.content) {
                        thinkingContent += data.content;
                        this.messages = this.messages.map(m => {
                            if (m.id === assistantMsgId) {
                                let steps = m.steps ? [...m.steps] : [];
                                
                                // Find or create thinking step
                                let thinkingStep = steps.find(s => s.type === 'thinking');
                                if (!thinkingStep) {
                                    // Create new thinking step
                                    thinkingStep = {
                                        id: `thinking-${Date.now()}`,
                                        type: 'thinking' as const,
                                        content: '',
                                        isFinished: false,
                                    };
                                    steps.push(thinkingStep);
                                } else {
                                    // Update existing thinking step
                                    const stepIndex = steps.findIndex(s => s.id === thinkingStep!.id);
                                    steps[stepIndex] = {
                                        ...thinkingStep,
                                        content: thinkingContent,
                                        isFinished: false,
                                    };
                                }
                                
                                return { ...m, steps };
                            }
                            return m;
                        });
                        onUpdate({ messages: this.messages, isThinking: true });
                    }
                } catch (e) {
                    console.warn('Failed to parse reasoning_delta event data', e);
                }
            });

            // Listen for 'delta' events for content updates
            eventSource.addEventListener('delta', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.content) {
                        lastDeltaTime = Date.now(); // Update timestamp
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
                        onUpdate({ messages: this.messages, isThinking: true, isWaitingForDeltas: false });
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

                    // Backend sends tool_calls as an array
                    const toolCalls: Array<{id?: string; name?: string; tool_name?: string; arguments?: any}> =
                        data.tool_calls || [data];

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

                            for (const tc of toolCalls) {
                                const newStep: MessageStep = {
                                    id: tc.id || `tool-${Date.now()}`,
                                    type: 'tool-call',
                                    toolName: tc.name || tc.tool_name,
                                    toolArgs: tc.arguments,
                                    toolStatus: 'running'
                                };
                                steps = [...steps, newStep];
                            }
                            return { ...m, steps };
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

            // Listen for 'confirm_request' events (for CONFIRM_REQUIRED tools)
            eventSource.addEventListener('confirm_request', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Confirm request received:', data);

                    this.messages = this.messages.map(m => {
                        if (m.id === assistantMsgId) {
                            let steps = m.steps ? [...m.steps] : [];

                            // Move existing content to a text step if needed
                            if (steps.length === 0 && fullContent) {
                                steps.push({
                                    id: `text-pre-${Date.now()}`,
                                    type: 'text',
                                    content: fullContent
                                });
                            }

                            // Add confirm request step
                            steps.push({
                                id: data.tool_call_id,
                                type: 'confirm-request',
                                toolCallId: data.tool_call_id,
                                toolName: data.tool_name,
                                confirmLabel: data.label,
                                confirmDescription: data.description,
                                confirmStatus: 'pending',
                            });
                            return { ...m, steps };
                        }
                        return m;
                    });
                    onUpdate({ messages: this.messages, isThinking: true });
                } catch (e) {
                    console.warn('Failed to parse confirm_request event data', e);
                }
            });

            // Listen for 'confirm_response' events (user's decision processed)
            eventSource.addEventListener('confirm_response', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Confirm response received:', data);

                    this.messages = this.messages.map(m => {
                        if (m.id === assistantMsgId && m.steps) {
                            const steps = m.steps.map(s => {
                                if (s.type === 'confirm-request' && s.toolCallId === data.tool_call_id) {
                                    return {
                                        ...s,
                                        confirmStatus: data.confirmed ? 'confirmed' : 'rejected',
                                    } as MessageStep;
                                }
                                return s;
                            });
                            return { ...m, steps };
                        }
                        return m;
                    });
                    // Keep thinking if confirmed (tool will execute), stop if rejected
                    onUpdate({ messages: this.messages, isThinking: data.confirmed });
                } catch (e) {
                    console.warn('Failed to parse confirm_response event data', e);
                }
            });

            // Listen for 'client_tool_call' events (for CLIENT_SIDE tools)
            eventSource.addEventListener('client_tool_call', (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Client tool call received:', data);

                    // Execute the tool based on tool_name
                    this.executeClientTool(data.tool_call_id, data.tool_name, data.arguments)
                        .then(() => {
                            console.log('Client tool executed successfully:', data.tool_name);
                        })
                        .catch((error) => {
                            console.error('Client tool execution failed:', error);
                        });
                } catch (e) {
                    console.warn('Failed to parse client_tool_call event data', e);
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
                isStreaming = false;
                if (deltaCheckInterval) {
                    clearInterval(deltaCheckInterval);
                    deltaCheckInterval = null;
                }
                
                // Mark thinking step as finished if it exists
                if (thinkingContent) {
                    this.messages = this.messages.map(m => {
                        if (m.id === assistantMsgId && m.steps) {
                            const steps = m.steps.map(s => {
                                if (s.type === 'thinking') {
                                    return { ...s, isFinished: true };
                                }
                                return s;
                            });
                            return { ...m, steps };
                        }
                        return m;
                    });
                }
                
                eventSource.close();
                onUpdate({ messages: this.messages, isThinking: false, isWaitingForDeltas: false });
            });

            // Handle errors
            eventSource.onerror = (err) => {
                isStreaming = false;
                if (deltaCheckInterval) {
                    clearInterval(deltaCheckInterval);
                    deltaCheckInterval = null;
                }
                console.error('EventSource failed:', err);
                eventSource.close();

                if (fullContent.length === 0) {
                    this.messages = this.messages.map(m =>
                        m.id === assistantMsgId ? { ...m, content: 'Error: Connection to stream failed.' } : m
                    );
                }
                onUpdate({ messages: this.messages, isThinking: false, isWaitingForDeltas: false });
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
        this.currentJobId = null;
        this.conversationId = null;
        onUpdate({ messages: [], isThinking: false });
    }

    setConversationId(id: string | null): void {
        this.conversationId = id;
    }

    getConversationId(): string | null {
        return this.conversationId;
    }

    async sendConfirmResponse(toolCallId: string, confirmed: boolean): Promise<void> {
        if (!this.accessToken || !this.currentJobId) {
            console.error('No access token or job ID available');
            return;
        }

        try {
            const response = await fetch(`${this.apiBaseUrl}/v1/confirm-response`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.accessToken}`,
                },
                body: JSON.stringify({
                    job_id: this.currentJobId,
                    tool_call_id: toolCallId,
                    confirmed,
                }),
            });

            if (!response.ok) {
                throw new Error(`Confirm response failed: ${response.status}`);
            }

            console.log('Confirm response sent:', { toolCallId, confirmed });
        } catch (error) {
            console.error('Failed to send confirm response:', error);
        }
    }

    private async executeClientTool(
        toolCallId: string,
        toolName: string,
        toolArguments: any
    ): Promise<void> {
        console.log('Executing client tool:', toolName, toolArguments);
        let result: string;

        try {
            // 1. Check custom handlers first
            if (this.customHandlers.has(toolName)) {
                const handler = this.customHandlers.get(toolName)!;
                const output = await handler(toolArguments);
                result = typeof output === 'string' ? output : JSON.stringify(output);
            }
            // 2. Built-in tools
            else {
                switch (toolName) {
                    case 'read_page_content':
                        // Notify that page reading has started
                        if (this.pageReadingCallback) {
                            this.pageReadingCallback(true);
                        }
                        result = await this.executeReadPageContent(toolArguments);
                        break;
                    case 'read_page_content_advanced':
                        // Advanced page reading with screenshots
                        if (this.pageReadingCallback) {
                            this.pageReadingCallback(true);
                        }
                        result = await this.executeReadPageContentAdvanced(toolArguments);
                        break;
                    case 'take_screenshot':
                        if (this.pageReadingCallback) {
                            this.pageReadingCallback(true);
                        }
                        result = await this.executeTakeScreenshot(toolArguments);
                        break;
                    case 'inspect_dom_element':
                        result = await this.executeInspectDomElement(toolArguments);
                        break;
                    default:
                        result = JSON.stringify({
                            error: 'unknown_tool',
                            message: `Client-side tool '${toolName}' is not implemented`,
                        });
                }
            }

            await this.submitToolResult(toolCallId, toolName, result);
        } catch (error: any) {
            console.error('Client tool execution error:', error);
            await this.submitToolResult(
                toolCallId,
                toolName,
                JSON.stringify({
                    error: 'execution_failed',
                    message: error.message || 'Unknown error',
                })
            );
        } finally {
            // Notify that page reading has ended
            if (
                ['read_page_content', 'read_page_content_advanced', 'take_screenshot'].includes(toolName) &&
                this.pageReadingCallback
            ) {
                this.pageReadingCallback(false);
            }
        }
    }

    private async submitToolResult(
        toolCallId: string,
        toolName: string,
        result: string
    ): Promise<void> {
        if (!this.accessToken || !this.currentJobId) {
            console.error('No access token or job ID available');
            return;
        }

        try {
            const response = await fetch(
                `${this.apiBaseUrl}/v1/tools/jobs/${this.currentJobId}/tool-results`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${this.accessToken}`,
                    },
                    body: JSON.stringify({
                        tool_call_id: toolCallId,
                        tool_name: toolName,
                        result: result,
                    }),
                }
            );

            if (!response.ok) {
                throw new Error(`Tool result submission failed: ${response.status}`);
            }

            console.log('Tool result submitted:', { toolCallId, toolName });
        } catch (error) {
            console.error('Failed to submit tool result:', error);
        }
    }

    private async executeReadPageContent(args: any): Promise<string> {
        const {
            selector = 'body',
            include_metadata = true,
            max_length = 50000,
        } = args;

        try {
            const element = document.querySelector(selector);
            if (!element) {
                return JSON.stringify({
                    error: 'element_not_found',
                    message: `No element found for selector: ${selector}`,
                });
            }

            let content = '';

            // Add metadata
            if (include_metadata) {
                content += `URL: ${window.location.href}\n`;
                content += `Title: ${document.title}\n`;
                content += `Timestamp: ${new Date().toISOString()}\n\n`;
                content += '--- PAGE CONTENT ---\n\n';
            }

            // Extract content with structure
            content += this.extractElementContent(element as HTMLElement, 0);

            // Truncate if needed
            if (content.length > max_length) {
                content = content.substring(0, max_length) + '\n\n[Content truncated]';
            }

            return JSON.stringify({
                success: true,
                content: content,
                length: content.length,
                truncated: content.length > max_length,
            });
        } catch (error: any) {
            return JSON.stringify({
                error: 'extraction_failed',
                message: error.message || 'Unknown error during DOM extraction',
            });
        }
    }

    private extractElementContent(element: HTMLElement, depth: number): string {
        const indent = '  '.repeat(depth);
        let result = '';

        // Skip invisible elements
        const style = window.getComputedStyle(element);
        if (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            style.opacity === '0'
        ) {
            return '';
        }

        // Skip unwanted elements
        const skipTags = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'IFRAME'];
        if (skipTags.includes(element.tagName)) {
            return '';
        }

        const tag = element.tagName;

        // Headings
        if (/^H[1-6]$/.test(tag)) {
            const level = parseInt(tag[1]);
            const text = element.textContent?.trim();
            if (text) {
                result += `${indent}${'#'.repeat(level)} ${text}\n\n`;
            }
            return result;
        }

        // Paragraphs
        if (tag === 'P') {
            const text = element.textContent?.trim();
            if (text) {
                result += `${indent}${text}\n\n`;
            }
            return result;
        }

        // Lists
        if (tag === 'UL' || tag === 'OL') {
            const items = element.querySelectorAll(':scope > li');
            items.forEach((li, idx) => {
                const marker = tag === 'OL' ? `${idx + 1}.` : '-';
                const text = li.textContent?.trim();
                if (text) {
                    result += `${indent}${marker} ${text}\n`;
                }
            });
            result += '\n';
            return result;
        }

        // Tables
        if (tag === 'TABLE') {
            result += `${indent}[Table]\n`;
            const rows = element.querySelectorAll('tr');
            rows.forEach((row) => {
                const cells = row.querySelectorAll('td, th');
                const cellTexts = Array.from(cells)
                    .map((cell) => cell.textContent?.trim())
                    .filter(t => t)
                    .join(' | ');
                if (cellTexts) {
                    result += `${indent}  ${cellTexts}\n`;
                }
            });
            result += '\n';
            return result;
        }

        // Landmarks
        if (['HEADER', 'NAV', 'MAIN', 'ARTICLE', 'SECTION', 'ASIDE', 'FOOTER'].includes(tag)) {
            result += `${indent}[${tag}]\n`;
            Array.from(element.children).forEach((child) => {
                result += this.extractElementContent(child as HTMLElement, depth + 1);
            });
            result += '\n';
            return result;
        }

        // Links
        if (tag === 'A') {
            const text = element.textContent?.trim();
            const href = element.getAttribute('href');
            if (text && href) {
                result += `${indent}[${text}](${href})\n`;
            }
            return result;
        }

        // Buttons and interactive elements
        if (['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) {
            const text = element.textContent?.trim() || (element as HTMLInputElement).value?.trim();
            if (text) {
                result += `${indent}[${tag}] ${text}\n`;
            }
            return result;
        }

        // Generic container - check if it has direct text content or recurse into children
        const hasChildren = element.children.length > 0;
        const directText = hasChildren ? '' : element.textContent?.trim();

        // If it's a leaf node with text (SPAN, DIV with only text, etc.), extract it
        if (!hasChildren && directText) {
            result += `${indent}${directText}\n`;
            return result;
        }

        // Otherwise, recurse into children
        if (hasChildren) {
            Array.from(element.children).forEach((child) => {
                result += this.extractElementContent(child as HTMLElement, depth);
            });
        }

        return result;
    }

    private async executeReadPageContentAdvanced(args: any): Promise<string> {
        try {
            const {
                selector = 'body',
                include_html = false,
                find_element_query,
                capture_screenshot = false,
                screenshot_selector,
                screenshot_query
            } = args;

            const result: any = {
                success: true,
                metadata: {
                    url: window.location.href,
                    title: document.title,
                    timestamp: new Date().toISOString(),
                },
                content: '',
                html: null,
                found_element: null,
                screenshot: null,
                screenshot_analysis_pending: false,
            };

            // 1. Find target element
            let targetElement: HTMLElement;
            if (find_element_query) {
                const found = this.findElementByQuery(find_element_query);
                if (found) {
                    targetElement = found;
                    result.found_element = {
                        query: find_element_query,
                        tag: found.tagName,
                        id: found.id || null,
                        classes: Array.from(found.classList),
                        text: found.textContent?.substring(0, 200),
                    };
                } else {
                    targetElement = document.querySelector(selector) as HTMLElement || document.body;
                    result.found_element = {
                        query: find_element_query,
                        found: false,
                        message: 'Element not found, using default selector',
                    };
                }
            } else {
                targetElement = document.querySelector(selector) as HTMLElement || document.body;
            }

            // 2. Extract content
            result.content = this.extractElementContent(targetElement, 0);

            // 3. Include HTML if requested
            if (include_html) {
                result.html = targetElement.outerHTML;
            }

            // 4. Capture screenshot if requested
            if (capture_screenshot) {
                try {
                    const screenshotElement = screenshot_selector
                        ? (document.querySelector(screenshot_selector) as HTMLElement || targetElement)
                        : targetElement;

                    const screenshot = await this.captureScreenshot(screenshotElement, false);
                    result.screenshot = screenshot;
                    result.screenshot_query = screenshot_query || 'Analyze this screenshot';
                    result.screenshot_analysis_pending = true; // Backend will analyze
                } catch (error: any) {
                    result.screenshot_error = error.message;
                }
            }

            return JSON.stringify(result);
        } catch (error: any) {
            return JSON.stringify({
                success: false,
                error: error.message || 'Unknown error during advanced page reading',
            });
        }
    }

    private findElementByQuery(query: string): HTMLElement | null {
        const lowerQuery = query.toLowerCase();

        // Search by text content
        const allElements = document.querySelectorAll('*');
        for (const el of Array.from(allElements)) {
            const text = el.textContent?.toLowerCase() || '';
            if (text.includes(lowerQuery) && text.length < 1000) {
                // Prefer shorter matches
                return el as HTMLElement;
            }
        }

        // Search by aria-label
        const ariaElement = document.querySelector(`[aria-label*="${query}" i]`);
        if (ariaElement) return ariaElement as HTMLElement;

        // Search by placeholder
        const placeholderElement = document.querySelector(`[placeholder*="${query}" i]`);
        if (placeholderElement) return placeholderElement as HTMLElement;

        // Search by button/link text
        const buttons = document.querySelectorAll('button, a');
        for (const btn of Array.from(buttons)) {
            if (btn.textContent?.toLowerCase().includes(lowerQuery)) {
                return btn as HTMLElement;
            }
        }

        return null;
    }

    private async captureScreenshot(element: HTMLElement, fullPage = false): Promise<string> {
        // Dynamically import html2canvas to avoid bundle bloat
        const html2canvas = (await import('html2canvas')).default;

        const canvas = await html2canvas(element, {
            useCORS: true,
            allowTaint: false,
            backgroundColor: '#ffffff',
            height: fullPage ? document.body.scrollHeight : undefined,
            windowHeight: fullPage ? document.body.scrollHeight : undefined,
        });

        // Convert to base64 (remove data URL prefix)
        const dataUrl = canvas.toDataURL('image/png');
        const base64 = dataUrl.split(',')[1];

        return base64;
    }

    private async executeTakeScreenshot(args: any): Promise<string> {
        try {
            const {
                selector,
                element_query,
                screenshot_query = 'Describe what is visible in this screenshot.',
                full_page = false,
            } = args;

            // Resolve target element
            let targetElement: HTMLElement = document.body;
            if (element_query) {
                const found = this.findElementByQuery(element_query);
                if (found) targetElement = found;
            } else if (selector) {
                const found = document.querySelector(selector) as HTMLElement;
                if (found) targetElement = found;
            }

            const screenshot = await this.captureScreenshot(targetElement, full_page);

            return JSON.stringify({
                success: true,
                screenshot,
                screenshot_query,
                screenshot_analysis_pending: true,
                metadata: {
                    url: window.location.href,
                    title: document.title,
                    timestamp: new Date().toISOString(),
                    element: targetElement === document.body ? 'viewport' : {
                        tag: targetElement.tagName,
                        id: targetElement.id || null,
                        classes: Array.from(targetElement.classList),
                    },
                },
            });
        } catch (error: any) {
            return JSON.stringify({
                success: false,
                error: error.message || 'Screenshot capture failed',
            });
        }
    }

    private async executeInspectDomElement(args: any): Promise<string> {
        try {
            const {
                selector,
                element_query,
                include_computed_styles = true,
                include_attributes = true,
                include_hierarchy = true,
                inspect_depth = 2,
            } = args;

            // Resolve target element
            let targetElement: HTMLElement | null = null;
            if (element_query) {
                targetElement = this.findElementByQuery(element_query);
            }
            if (!targetElement && selector) {
                targetElement = document.querySelector(selector) as HTMLElement;
            }
            if (!targetElement) {
                return JSON.stringify({
                    success: false,
                    error: 'Element not found',
                    query: element_query,
                    selector,
                });
            }

            const rect = targetElement.getBoundingClientRect();
            const result: any = {
                success: true,
                element: {
                    tag: targetElement.tagName,
                    id: targetElement.id || null,
                    classes: Array.from(targetElement.classList),
                    text_content: targetElement.textContent?.trim().substring(0, 500),
                },
                bounding_rect: {
                    top: rect.top,
                    left: rect.left,
                    width: rect.width,
                    height: rect.height,
                },
            };

            if (include_attributes) {
                const attrs: Record<string, string> = {};
                for (const attr of Array.from(targetElement.attributes)) {
                    attrs[attr.name] = attr.value;
                }
                result.attributes = attrs;
                result.accessibility = {
                    role: targetElement.getAttribute('role'),
                    aria_label: targetElement.getAttribute('aria-label'),
                    aria_labelledby: targetElement.getAttribute('aria-labelledby'),
                    aria_describedby: targetElement.getAttribute('aria-describedby'),
                    tabindex: targetElement.getAttribute('tabindex'),
                };
            }

            if (include_computed_styles) {
                const cs = window.getComputedStyle(targetElement);
                result.computed_styles = {
                    display: cs.display,
                    position: cs.position,
                    visibility: cs.visibility,
                    opacity: cs.opacity,
                    width: cs.width,
                    height: cs.height,
                    margin: cs.margin,
                    padding: cs.padding,
                    background_color: cs.backgroundColor,
                    color: cs.color,
                    font_size: cs.fontSize,
                    font_weight: cs.fontWeight,
                    border: cs.border,
                    z_index: cs.zIndex,
                    overflow: cs.overflow,
                    flex_direction: cs.flexDirection,
                    grid_template_columns: cs.gridTemplateColumns,
                };
            }

            if (include_hierarchy) {
                const ancestors: any[] = [];
                let current = targetElement.parentElement;
                while (current && current !== document.body) {
                    ancestors.push({
                        tag: current.tagName,
                        id: current.id || null,
                        classes: Array.from(current.classList).slice(0, 5),
                    });
                    current = current.parentElement;
                }
                result.hierarchy = ancestors;
            }

            result.children = this.inspectChildren(targetElement, inspect_depth);

            return JSON.stringify(result);
        } catch (error: any) {
            return JSON.stringify({
                success: false,
                error: error.message || 'DOM inspection failed',
            });
        }
    }

    private inspectChildren(element: HTMLElement, depth: number): any[] {
        if (depth <= 0) return [];
        return Array.from(element.children).slice(0, 20).map((child) => {
            const el = child as HTMLElement;
            const entry: any = {
                tag: el.tagName,
                id: el.id || null,
                classes: Array.from(el.classList).slice(0, 5),
                text: el.textContent?.trim().substring(0, 100),
            };
            if (depth > 1 && el.children.length > 0) {
                entry.children = this.inspectChildren(el, depth - 1);
            }
            return entry;
        });
    }

    async getConversations(offset = 0, limit = 50): Promise<import('./types').ConversationListResponse> {
        if (!this.accessToken) throw new Error('No access token available');
        const response = await fetch(
            `${this.apiBaseUrl}/v1/conversations?offset=${offset}&limit=${limit}`,
            { headers: { 'Authorization': `Bearer ${this.accessToken}` } }
        );
        if (!response.ok) throw new Error(`Failed to fetch conversations: ${response.status}`);
        return response.json();
    }

    async loadConversation(id: string, onUpdate: (state: ChatState) => void): Promise<void> {
        if (!this.accessToken) throw new Error('No access token available');
        const response = await fetch(
            `${this.apiBaseUrl}/v1/conversations/${id}`,
            { headers: { 'Authorization': `Bearer ${this.accessToken}` } }
        );
        if (!response.ok) throw new Error(`Failed to load conversation: ${response.status}`);
        const detail: import('./types').ConversationDetail = await response.json();

        // Map conversation messages to MessageProps, merging consecutive assistant messages from same job
        const msgs: MessageProps[] = [];
        let currentAssistantMsg: MessageProps | null = null;
        let currentJobId: string | null = null;

        for (const m of detail.messages) {
            // Skip tool messages
            if (m.role !== 'user' && m.role !== 'assistant') {
                continue;
            }

            if (m.role === 'user') {
                // Flush any pending assistant message
                if (currentAssistantMsg) {
                    msgs.push(currentAssistantMsg);
                    currentAssistantMsg = null;
                    currentJobId = null;
                }

                const userMsg: MessageProps = {
                    id: m.id,
                    role: 'user',
                    content: m.content || '',
                    shouldAnimate: false,
                };

                // Map attachments if present
                if (m.attachments && m.attachments.length > 0) {
                    userMsg.attachments = m.attachments.map(att => ({
                        id: att.id,
                        type: att.type as 'image' | 'file',
                        url: `${this.apiBaseUrl}${att.url}`,
                        name: att.name,
                        size: att.size,
                        contentType: att.content_type,
                    }));
                }

                msgs.push(userMsg);
            } else if (m.role === 'assistant') {
                // Merge consecutive assistant messages from same job
                if (currentAssistantMsg && currentJobId === m.job_id) {
                    // Append content to existing message
                    currentAssistantMsg.content = (currentAssistantMsg.content || '') + (m.content || '');
                } else {
                    // Flush previous assistant message
                    if (currentAssistantMsg) {
                        msgs.push(currentAssistantMsg);
                    }
                    // Start new assistant message
                    currentAssistantMsg = {
                        id: m.id,
                        role: 'assistant',
                        content: m.content || '',
                        shouldAnimate: false,
                    };
                    currentJobId = m.job_id;
                }
            }
        }

        // Flush any remaining assistant message
        if (currentAssistantMsg) {
            msgs.push(currentAssistantMsg);
        }

        this.messages = msgs;
        this.conversationId = id;
        this.currentJobId = null;
        onUpdate({ messages: this.messages, isThinking: false });
    }

    async deleteConversation(id: string): Promise<void> {
        if (!this.accessToken) throw new Error('No access token available');
        const response = await fetch(
            `${this.apiBaseUrl}/v1/conversations/${id}`,
            { method: 'DELETE', headers: { 'Authorization': `Bearer ${this.accessToken}` } }
        );
        if (!response.ok) throw new Error(`Failed to delete conversation: ${response.status}`);
    }

    async searchConversations(query: string, offset = 0, limit = 20): Promise<import('./types').ConversationListResponse> {
        if (!this.accessToken) throw new Error('No access token available');
        const response = await fetch(
            `${this.apiBaseUrl}/v1/conversations/search?q=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`,
            { headers: { 'Authorization': `Bearer ${this.accessToken}` } }
        );
        if (!response.ok) throw new Error(`Failed to search conversations: ${response.status}`);
        return response.json();
    }

    async uploadFile(file: File): Promise<AttachedFile> {
        if (!this.accessToken) {
            throw new Error('No access token available');
        }

        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${this.apiBaseUrl}/v1/files/upload`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${this.accessToken}`,
            },
            body: formData,
        });

        if (!response.ok) {
            let errorDetails = response.statusText;
            try {
                const errorJson = await response.json();
                errorDetails = errorJson.detail || JSON.stringify(errorJson);
            } catch (e) { /* ignore */ }
            throw new Error(`File upload failed: ${errorDetails}`);
        }

        const result = await response.json();
        return {
            file_id: result.file_id,
            filename: result.filename,
            content_type: result.content_type,
            size_bytes: result.size_bytes,
        };
    }
}
