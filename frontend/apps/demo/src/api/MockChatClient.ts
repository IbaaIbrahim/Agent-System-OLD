import { ChatClient, ChatState } from './types';
import { MessageProps, MessageStep } from '@chatbot-ui/core';

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

        // Signal intent to think. Queue is BLOCKED by isThinking=true.
        onUpdate({ messages: this.messages, isThinking: true });

        // 2. Add Assistant Message with INITIAL THINKING step
        const assistantId = (Date.now() + 1).toString();

        // Scenario Selection based on input
        if (content.toLowerCase().includes('fail') || content.toLowerCase().includes('error')) {
            await this.runGenericScenario(assistantId, content, onUpdate, true);
        } else if (content.toLowerCase().includes('stream')) {
            await this.runComplexSSEMock(assistantId, onUpdate);
        } else {
            await this.runSearchScenario(assistantId, content, onUpdate);
        }
    }

    // specific mock demo for the real scenario
    // tool.request for search in sse stream
    // tool.request for thinking in sse stream
    // agent.delta start for streaming text
    // tool.completed for thinking
    // tool.completed for search
    // agent.delta completed for streaming text
    // tool.request for search again
    // tool.completed for search
    // agent.delta start for text streaming 
    // agent.delta completed for text streaming
    // job.completed
    private async runComplexSSEMock(id: string, onUpdate: (state: ChatState) => void) {
        // Shared state accessor
        let steps: MessageStep[] = [];
        const update = (isThinking: boolean) => this.updateMsg(id, steps, onUpdate, isThinking);

        // 1. tool.request for search
        steps.push({
            id: 'ws-search-1',
            type: 'tool-call',
            toolName: 'search_web',
            toolArgs: { query: 'latest agent patterns' },
            toolStatus: 'running'
        });
        update(true);
        await this.delay(800);

        // 2. tool.request for thinking
        steps.push({
            id: 'ws-thinking-1',
            type: 'thinking',
            content: 'Planning search strategy...',
            thoughts: []
        });
        update(true);
        await this.delay(500);

        // Update Thinking with Logs
        const thinkIdx = steps.findIndex(s => s.id === 'ws-thinking-1');
        if (thinkIdx !== -1) {
            steps[thinkIdx].thoughts = ['Checking query patterns...'];
            update(true);
            await this.delay(400);

            steps[thinkIdx].thoughts?.push('Identifying key entities: "agent patterns"...');
            update(true);
            await this.delay(400);
        }

        // 3. agent.delta start for streaming text

        // 3. agent.delta start for streaming text
        // Note: In our UI, text steps usually appear AFTER thinking, but if the stream sends them here, we append them here.
        // User said "agent.delta start", let's assume it's an intro text.
        steps.push({
            id: 'ws-text-1',
            type: 'text',
            content: 'I will checking the '
        });
        update(true);
        await this.delay(500);

        // Stream a bit more
        const textStepIndex = steps.findIndex(s => s.id === 'ws-text-1');
        steps[textStepIndex].content += 'latest sources for you.';
        update(true);
        await this.delay(500);

        // 4. tool.completed for thinking
        const thinkingIndex = steps.findIndex(s => s.id === 'ws-thinking-1');
        steps[thinkingIndex] = { ...steps[thinkingIndex], isFinished: true, content: 'Strategy planned' };
        update(true);
        await this.delay(500);

        // 5. tool.completed for search
        const searchIndex = steps.findIndex(s => s.id === 'ws-search-1');
        steps[searchIndex] = { ...steps[searchIndex], toolStatus: 'completed' };
        update(true);
        await this.delay(500);

        // 6. agent.delta completed for streaming text
        // (Visual completion is implicit, but we can verify text is done)
        steps[textStepIndex].content += '\nSearch complete.';
        // We don't mark text as "finished" with a flag, it just stops growing.
        update(true);
        await this.delay(1000);

        // 7. tool.request for search again
        steps.push({
            id: 'ws-search-2',
            type: 'tool-call',
            toolName: 'search_web',
            toolArgs: { query: 'agent implementation details' },
            toolStatus: 'running'
        });
        update(true);
        await this.delay(1000);

        // 8. tool.completed for search
        const search2Index = steps.findIndex(s => s.id === 'ws-search-2');
        steps[search2Index] = { ...steps[search2Index], toolStatus: 'completed' };
        update(true);
        await this.delay(500);

        // 9. agent.delta start for text streaming 
        steps.push({
            id: 'ws-text-2',
            type: 'text',
            content: 'Based on the '
        });
        update(true);
        await this.delay(300);

        // 10. agent.delta completed (simulated streaming)
        const finalContent = "Based on the gathered information, here is a detailed breakdown of the agentic patterns found:\n- **ReAct Pattern**: Interleaving thought and action.\n- **Plan-and-Solve**: Explicit planning step before execution.\n- **Reflexion**: Self-correction loops.";

        // Quick stream simulation
        const text2Index = steps.findIndex(s => s.id === 'ws-text-2');
        steps[text2Index].content = finalContent; // In real app this would be chunked
        update(true);
        await this.delay(1000);

        // 11. job.completed (Queue Release)
        update(false);
    }

    // Standard Success Scenario
    private async runSearchScenario(id: string, query: string, onUpdate: (state: ChatState) => void) {
        // Step A: Thinking
        let currentSteps: MessageStep[] = [
            { id: 'step-1', type: 'thinking', content: 'Understanding request...' }
        ];
        this.updateMsg(id, currentSteps, onUpdate, true);

        // Delay
        await this.delay(1500);

        // Step B: Tool Requested (Running)
        // Mark thinking as finished
        currentSteps = [
            { id: 'step-1', type: 'thinking', content: 'Request understood', isFinished: true },
            {
                id: 'step-2',
                type: 'tool-call',
                toolName: 'search_web',
                toolArgs: { query },
                toolStatus: 'running' // Emulating "tool.requested"
            }
        ];
        this.updateMsg(id, currentSteps, onUpdate, true);

        // Delay (Network time)
        await this.delay(2000);

        // Step C: Tool Completed
        currentSteps = [
            { id: 'step-1', type: 'thinking', content: 'Request understood', isFinished: true },
            {
                id: 'step-2',
                type: 'tool-call',
                toolName: 'search_web',
                toolArgs: { query },
                toolStatus: 'completed' // Emulating "tool.completed"
            },
            { id: 'step-3', type: 'thinking', content: 'Reading search results...' }
        ];
        this.updateMsg(id, currentSteps, onUpdate, true);

        // Delay (Reading time)
        await this.delay(1500);

        // Step D: Final Response
        const responseText = `I have completed the search for "${query}". The results indicate a strong trend towards modular agent architectures. Key findings include:
1. Separation of concerns (Planning vs Execution).
2. Explicit state management for tools.
3. Visual feedback for user trust.

I have updated the interface to reflect these patterns.`;

        currentSteps = [
            { id: 'step-1', type: 'thinking', content: 'Request understood', isFinished: true },
            {
                id: 'step-2',
                type: 'tool-call',
                toolName: 'search_web',
                toolArgs: { query },
                toolStatus: 'completed'
            },
            { id: 'step-3', type: 'thinking', content: 'Analysis complete', isFinished: true },
            { id: 'step-4', type: 'text', content: responseText }
        ];

        // Release Lock (isThinking = false)
        this.updateMsg(id, currentSteps, onUpdate, false);
    }

    // Failure Scenario
    private async runGenericScenario(id: string, query: string, onUpdate: (state: ChatState) => void, shouldFail: boolean) {
        // Step A: Thinking
        let currentSteps: MessageStep[] = [
            { id: 'step-1', type: 'thinking', content: 'Initializing...' }
        ];
        this.updateMsg(id, currentSteps, onUpdate, true);
        await this.delay(1000);

        // Step B: Tool Run
        currentSteps = [
            { id: 'step-1', type: 'thinking', content: 'Initialized', isFinished: true },
            {
                id: 'step-2',
                type: 'tool-call',
                toolName: 'system_check',
                toolArgs: { target: 'database' },
                toolStatus: 'running'
            }
        ];
        this.updateMsg(id, currentSteps, onUpdate, true);
        await this.delay(1500);

        // Step C: Fail
        if (shouldFail) {
            currentSteps = [
                { id: 'step-1', type: 'thinking', content: 'Initialized', isFinished: true },
                {
                    id: 'step-2',
                    type: 'tool-call',
                    toolName: 'system_check',
                    toolArgs: { target: 'database' },
                    toolStatus: 'failed'  // Emulating "tool.failed"
                },
                { id: 'step-3', type: 'text', content: 'I encountered an error while checking the database. Please try again later.' }
            ];
            // Release Lock
            this.updateMsg(id, currentSteps, onUpdate, false);
            return;
        }
    }

    private updateMsg(id: string, steps: MessageStep[], onUpdate: (state: ChatState) => void, isThinking: boolean) {
        // Only append/update if message exists, else append
        const exists = this.messages.some(m => m.id === id);
        if (exists) {
            this.messages = this.messages.map(m => m.id === id ? { ...m, steps } : m);
        } else {
            const assistantMsg: MessageProps = {
                id,
                role: 'assistant',
                content: '',
                steps
            };
            this.messages = [...this.messages, assistantMsg];
        }
        onUpdate({ messages: this.messages, isThinking });
    }

    private delay(ms: number) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    reset(onUpdate: (state: ChatState) => void): void {
        this.messages = [];
        onUpdate({ messages: [], isThinking: false });
    }

    setModel(model: string | null): void {
        // Mock client doesn't use the model, but we implement the interface
    }
}
