# Unified Tool Management System - Implementation Plan

> **Status: ✅ IMPLEMENTED** (February 2026)
>
> All 8 phases have been implemented. See the Key Files Summary at the bottom for file locations.

## Overview

Create a comprehensive tool management system that:
1. Makes the checklist generator self-contained (removes `/old/` folder dependency)
2. Adds "confirm before execute" pattern for tools requiring user approval
3. Establishes a unified tool configuration system with clear behavior types
4. All tool execution handled exclusively by tool-workers (not gateway or orchestrator)

## Architecture

```
Frontend                     API Gateway              Orchestrator (Brain)       Tool Workers
   │                              │                        │                          │
   │  POST /chat/completions      │                        │                          │
   ├─────────────────────────────>│                        │                          │
   │                              │   Kafka: agent.jobs    │                          │
   │                              ├───────────────────────>│                          │
   │                              │                        │                          │
   │                              │                        │  LLM decides tool call   │
   │                              │                        ├─────────────────────────>│
   │                              │                        │                          │
   │                              │                        │  (AUTO_EXECUTE tools)    │
   │                              │                        │  Kafka: agent.tools      │
   │                              │                        ├─────────────────────────>│
   │                              │                        │                          │ Execute & validate
   │                              │                        │<─────────tool_result─────│ (plan access, exists)
   │<─────────────────────────────────SSE: tool_result─────│                          │
   │                              │                        │                          │
   │                              │                        │  (CONFIRM_REQUIRED tools)│
   │<─────────────────────────────────SSE: confirm_request─│                          │
   │                              │                        │                          │
   │  User clicks Confirm/Reject  │                        │                          │
   ├─────────────────────────────>│  Kafka: agent.confirm  │                          │
   │                              ├───────────────────────>│                          │
   │                              │                        │  Kafka: agent.tools      │
   │                              │                        ├─────────────────────────>│
   │                              │                        │<─────────tool_result─────│
   │<─────────────────────────────────SSE: tool_result─────│                          │
```

## Responsibility Separation

| Component | Responsibility |
|-----------|----------------|
| **API Gateway** | Pass requests to Kafka. No tool logic. |
| **Orchestrator (Brain)** | Run agent loop, call tools via Kafka. For CONFIRM_REQUIRED tools, emit `confirm_request` to client first. |
| **Tool Workers** | Handle ALL tool execution: validate tool exists, check plan access, execute, return result or error. |
| **Frontend** | Display confirm buttons, send user decisions back. |

## Tool Behavior Types

| Behavior | Description | Plan Required | Example Tools |
|----------|-------------|---------------|---------------|
| `AUTO_EXECUTE` | Executes automatically when LLM calls it | Yes (plan-based) | `code_executor` |
| `USER_ENABLED` | Plan-based + requires user to toggle ON in UI | Yes (plan-based) | `web_search` |
| `CONFIRM_REQUIRED` | Brain sends confirm request to client, waits for user approval per-call | Yes (plan-based) | `generate_checklist` |
| `CLIENT_SIDE` | Sent to frontend for local execution | No | `read_page`, `get_element` |

### Behavior Comparison

| Behavior | Plan Access | User Toggle | Per-Call Confirm |
|----------|-------------|-------------|------------------|
| `AUTO_EXECUTE` | ✓ Required | ✗ Always on | ✗ Auto |
| `USER_ENABLED` | ✓ Required | ✓ Must enable | ✗ Auto |
| `CONFIRM_REQUIRED` | ✓ Required | ✗ Always on | ✓ Must confirm |
| `CLIENT_SIDE` | ✗ N/A | ✗ N/A | ✗ Frontend |

**Note:** All backend tools (`AUTO_EXECUTE`, `USER_ENABLED`, `CONFIRM_REQUIRED`) are plan-based. Tool availability is determined by:
1. Tenant's subscription plan (validated by workers)
2. User's enabled tools preference (for `USER_ENABLED` tools, sent from frontend)

---

## Phase 1: Self-Contained Checklist Generator

### Files to Create

**1. Asset folder structure:**
```
services/tool-workers/src/tools/assets/
├── __init__.py
├── loader.py
└── checklist_generator/
    ├── schema.json        # Copy from old/constants/data/
    └── system_prompt.txt  # Copy from old/constants/data/
```

**2. Asset loader utility: [loader.py](services/tool-workers/src/tools/assets/loader.py)**
```python
from pathlib import Path
import json

ASSETS_DIR = Path(__file__).parent

def load_json_asset(tool_name: str, filename: str) -> dict:
    path = ASSETS_DIR / tool_name / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_text_asset(tool_name: str, filename: str) -> str:
    path = ASSETS_DIR / tool_name / filename
    with open(path, encoding="utf-8") as f:
        return f.read().strip()
```

### Files to Modify

**[checklist_generator.py](services/tool-workers/src/tools/checklist_generator.py)**
- Change lines 14-17: Replace `/old/` path references with asset loader
- Use `load_json_asset("checklist_generator", "schema.json")`
- Use `load_text_asset("checklist_generator", "system_prompt.txt")`

---

## Phase 2: Unified Tool Configuration System

### Files to Create

**NEW: [libs/common/tool_catalog.py](libs/common/tool_catalog.py)**
```python
"""Unified tool catalog - single source of truth for all tool configurations."""

from enum import Enum
from pydantic import BaseModel


class ToolBehavior(str, Enum):
    """Tool behavior types determining execution flow."""
    AUTO_EXECUTE = "auto_execute"          # Executes automatically (plan-based, always on)
    USER_ENABLED = "user_enabled"          # Plan-based + user must toggle on in UI
    CONFIRM_REQUIRED = "confirm_required"  # Requires user confirmation per-call
    CLIENT_SIDE = "client_side"            # Executes in frontend


class ToolMetadata(BaseModel):
    """Tool configuration metadata."""
    name: str
    description: str
    behavior: ToolBehavior
    # Plan requirement (which plans include this tool)
    required_plan_feature: str | None = None  # e.g., "tools.web_search", "tools.checklist"
    # For USER_ENABLED tools - UI display
    toggle_label: str | None = None  # e.g., "Web Search"
    toggle_description: str | None = None  # e.g., "Allow agent to search the web"
    # For CONFIRM_REQUIRED tools
    confirm_button_label: str | None = None
    confirm_description_template: str | None = None


# Master catalog - workers use this to validate and route
TOOL_CATALOG: dict[str, ToolMetadata] = {
    "web_search": ToolMetadata(
        name="web_search",
        description="Search the web for information",
        behavior=ToolBehavior.USER_ENABLED,
        required_plan_feature="tools.web_search",
        toggle_label="Web Search",
        toggle_description="Allow agent to search the web for information",
    ),
    "code_executor": ToolMetadata(
        name="code_executor",
        description="Execute Python code in sandbox",
        behavior=ToolBehavior.AUTO_EXECUTE,
        required_plan_feature="tools.code_executor",
    ),
    "generate_checklist": ToolMetadata(
        name="generate_checklist",
        description="Generate a structured Flowdit checklist",
        behavior=ToolBehavior.CONFIRM_REQUIRED,
        required_plan_feature="tools.checklist_generator",
        confirm_button_label="Generate Checklist",
        confirm_description_template="Create '{title}' checklist with {context}",
    ),
}


def get_tool_metadata(tool_name: str) -> ToolMetadata | None:
    """Get metadata for a tool by name."""
    return TOOL_CATALOG.get(tool_name)


def get_tools_for_plan(plan_features: list[str]) -> list[ToolMetadata]:
    """Get all tools available for a given plan's features."""
    return [
        tool for tool in TOOL_CATALOG.values()
        if tool.required_plan_feature is None
        or tool.required_plan_feature in plan_features
    ]
```

### Files to Modify

**[base.py](services/tool-workers/src/tools/base.py)**
```python
class ToolBehavior(str, Enum):
    AUTO_EXECUTE = "auto_execute"      # Always on when plan allows
    USER_ENABLED = "user_enabled"      # Requires user toggle in UI
    CONFIRM_REQUIRED = "confirm_required"
    CLIENT_SIDE = "client_side"


class BaseTool(ABC):
    name: str = "base_tool"
    description: str = "Base tool description"
    parameters: dict[str, Any] = {}
    behavior: ToolBehavior = ToolBehavior.AUTO_EXECUTE
    required_plan_feature: str | None = None  # Plan feature required to use this tool
```

---

## Phase 3: Tool Workers - Validation & Error Handling

Workers are responsible for ALL tool validation and execution.

### Files to Modify

**[main.py](services/tool-workers/src/main.py)** - Update tool request handler:

```python
async def handle_tool_request(message, headers):
    tool_name = message["tool_name"]
    arguments = message.get("arguments", {})
    job_id = message["job_id"]
    tenant_id = message["tenant_id"]
    tool_call_id = message["tool_call_id"]
    plan_features = message.get("plan_features", [])  # From ITT v2
    enabled_tools = message.get("enabled_tools", [])  # From frontend (user toggles)

    registry = ToolRegistry()
    tool = registry.get_tool(tool_name)

    # 1. Check if tool exists
    if tool is None:
        result = json.dumps({
            "error": "tool_not_found",
            "message": f"Tool '{tool_name}' does not exist",
            "success": False,
        })
        await store_result_and_resume(tool_call_id, job_id, result, status="failed")
        return

    # 2. Check plan access
    if tool.required_plan_feature and tool.required_plan_feature not in plan_features:
        result = json.dumps({
            "error": "plan_access_denied",
            "message": f"Tool '{tool_name}' requires plan feature: {tool.required_plan_feature}",
            "success": False,
        })
        await store_result_and_resume(tool_call_id, job_id, result, status="failed")
        return

    # 3. Check user enabled (for USER_ENABLED tools)
    if tool.behavior == ToolBehavior.USER_ENABLED and tool_name not in enabled_tools:
        result = json.dumps({
            "error": "tool_not_enabled",
            "message": f"Tool '{tool_name}' is not enabled by user",
            "success": False,
        })
        await store_result_and_resume(tool_call_id, job_id, result, status="failed")
        return

    # 4. Execute tool
    try:
        result = await tool.execute(arguments, context={...})
        await store_result_and_resume(tool_call_id, job_id, result, status="completed")
    except Exception as e:
        result = json.dumps({
            "error": "execution_failed",
            "message": str(e),
            "success": False,
        })
        await store_result_and_resume(tool_call_id, job_id, result, status="failed")
```

---

## Phase 4: Orchestrator - Confirm Request Flow

The brain checks tool behavior BEFORE dispatching to workers. For `CONFIRM_REQUIRED` tools, it sends a confirm request to the client instead.

### Files to Modify

**[tool_handler.py](services/orchestrator/src/handlers/tool_handler.py)**

```python
from libs.common.tool_catalog import get_tool_metadata, ToolBehavior

async def dispatch_tools(self, state, tool_calls):
    """Dispatch tool calls based on their behavior."""
    for tc in tool_calls:
        metadata = get_tool_metadata(tc.name)

        # Unknown tools - dispatch anyway, workers will handle error
        if metadata is None:
            await self._dispatch_to_kafka(state, tc)
            continue

        if metadata.behavior == ToolBehavior.CONFIRM_REQUIRED:
            # Send confirm request to client, suspend job
            await self._emit_confirm_request(state, tc, metadata)
            # Job suspends, waiting for user confirmation

        elif metadata.behavior == ToolBehavior.CLIENT_SIDE:
            # Emit client tool call, frontend handles execution
            await self._emit_client_tool_call(state, tc)

        else:  # AUTO_EXECUTE or USER_ENABLED
            # Dispatch to workers (workers validate plan + user-enabled)
            await self._dispatch_to_kafka(state, tc)


async def _emit_confirm_request(self, state, tool_call, metadata):
    """Emit confirm request event to client."""
    # Generate description from template
    description = metadata.confirm_description_template
    if description and tool_call.arguments:
        description = description.format(**tool_call.arguments)

    event = {
        "type": "confirm_request",
        "tool_call_id": tool_call.id,
        "tool_name": tool_call.name,
        "label": metadata.confirm_button_label or f"Run {tool_call.name}",
        "description": description,
        "arguments": tool_call.arguments,
    }
    await self.event_publisher.publish(state.job_id, event)
```

### New SSE Event Types

| Event | Payload | Purpose |
|-------|---------|---------|
| `confirm_request` | `{tool_call_id, tool_name, label, description, arguments}` | Display confirm/reject buttons |
| `confirm_response` | `{tool_call_id, confirmed: bool}` | User's decision |

---

## Phase 5: API Gateway - Confirm Response Endpoint

Gateway just passes the confirmation to Kafka - no tool logic.

### Files to Modify

**[chat.py](services/api-gateway/src/routers/chat.py)**

```python
class ConfirmResponseRequest(BaseModel):
    job_id: str
    tool_call_id: str
    confirmed: bool


@router.post("/confirm-response")
async def confirm_response(
    body: ConfirmResponseRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Pass user's confirm/reject decision to orchestrator via Kafka."""
    await producer.send(
        topic="agent.confirm",
        message={
            "job_id": body.job_id,
            "tool_call_id": body.tool_call_id,
            "confirmed": body.confirmed,
            "tenant_id": str(tenant_id),
        },
        key=body.job_id,
    )
    return {"status": "received"}
```

---

## Phase 6: Orchestrator - Handle Confirm Response

### Files to Create

**NEW: [confirm_handler.py](services/orchestrator/src/handlers/confirm_handler.py)**

```python
"""Handler for user confirmation responses."""

class ConfirmHandler:
    """Handles user confirm/reject responses for CONFIRM_REQUIRED tools."""

    async def handle_confirmation(self, message):
        job_id = message["job_id"]
        tool_call_id = message["tool_call_id"]
        confirmed = message["confirmed"]

        # Load job snapshot
        state = await self.snapshot_service.load_snapshot(job_id)

        if confirmed:
            # User approved - dispatch to workers
            tool_call = self._find_pending_tool_call(state, tool_call_id)
            await self.tool_handler.dispatch_to_kafka(state, tool_call)

            # Emit confirmation event
            await self.event_publisher.publish(job_id, {
                "type": "confirm_response",
                "tool_call_id": tool_call_id,
                "confirmed": True,
            })
        else:
            # User rejected - resume with rejection result
            result = json.dumps({
                "error": "user_rejected",
                "message": "User cancelled this action",
                "success": False,
            })
            await self._inject_tool_result(state, tool_call_id, result)
            await self._resume_agent(state)

            await self.event_publisher.publish(job_id, {
                "type": "confirm_response",
                "tool_call_id": tool_call_id,
                "confirmed": False,
            })
```

---

## Phase 7: Frontend - Confirm Buttons

### New Component

**NEW: [ConfirmButtons.tsx](frontend/packages/chatbot-ui/src/components/ConfirmButtons/ConfirmButtons.tsx)**

```tsx
interface ConfirmButtonsProps {
    toolCallId: string;
    toolName: string;
    label: string;
    description?: string;
    status: 'pending' | 'confirmed' | 'rejected' | 'executing';
    onConfirm: (toolCallId: string) => void;
    onReject: (toolCallId: string) => void;
}

export const ConfirmButtons: React.FC<ConfirmButtonsProps> = ({
    toolCallId, toolName, label, description, status, onConfirm, onReject
}) => {
    if (status !== 'pending') {
        return (
            <div className="cb-confirm-status">
                {status === 'confirmed' && <span>✓ Confirmed - executing...</span>}
                {status === 'rejected' && <span>✗ Cancelled</span>}
            </div>
        );
    }

    return (
        <div className="cb-confirm-buttons">
            <div className="cb-confirm-header">
                <span className="cb-tool-icon">🔧</span>
                <span className="cb-tool-label">{label}</span>
            </div>
            {description && <p className="cb-confirm-description">{description}</p>}
            <div className="cb-confirm-actions">
                <button
                    className="cb-btn-confirm"
                    onClick={() => onConfirm(toolCallId)}
                >
                    Confirm
                </button>
                <button
                    className="cb-btn-reject"
                    onClick={() => onReject(toolCallId)}
                >
                    Cancel
                </button>
            </div>
        </div>
    );
};
```

### Files to Modify

**[RealChatClient.ts](frontend/apps/demo/src/api/RealChatClient.ts)**

```typescript
// Add after line 220 (after tool_result listener)

eventSource.addEventListener('confirm_request', (event: MessageEvent) => {
    const data = JSON.parse(event.data);
    console.log('Confirm request received:', data);

    this.messages = this.messages.map(m => {
        if (m.id === assistantMsgId) {
            let steps = m.steps ? [...m.steps] : [];
            steps.push({
                id: data.tool_call_id,
                type: 'confirm-request',
                toolCallId: data.tool_call_id,
                toolName: data.tool_name,
                label: data.label,
                description: data.description,
                confirmStatus: 'pending',
            });
            return { ...m, steps };
        }
        return m;
    });
    onUpdate({ messages: this.messages, isThinking: true });
});

eventSource.addEventListener('confirm_response', (event: MessageEvent) => {
    const data = JSON.parse(event.data);

    this.messages = this.messages.map(m => {
        if (m.id === assistantMsgId && m.steps) {
            const steps = m.steps.map(s => {
                if (s.type === 'confirm-request' && s.toolCallId === data.tool_call_id) {
                    return {
                        ...s,
                        confirmStatus: data.confirmed ? 'confirmed' : 'rejected',
                    };
                }
                return s;
            });
            return { ...m, steps };
        }
        return m;
    });
    onUpdate({ messages: this.messages, isThinking: data.confirmed });
});

// Add method for sending confirmation
async sendConfirmResponse(jobId: string, toolCallId: string, confirmed: boolean): Promise<void> {
    await fetch(`${GATEWAY_URL}/v1/confirm-response`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.accessToken}`,
        },
        body: JSON.stringify({ job_id: jobId, tool_call_id: toolCallId, confirmed }),
    });
}
```

**[types.ts](frontend/apps/demo/src/api/types.ts)**

```typescript
export type MessageStepType = 'text' | 'thinking' | 'tool-call' | 'confirm-request';

export interface ConfirmRequestStep {
    id: string;
    type: 'confirm-request';
    toolCallId: string;
    toolName: string;
    label: string;
    description?: string;
    confirmStatus: 'pending' | 'confirmed' | 'rejected' | 'executing';
}
```

**[MessageBubble.tsx](frontend/packages/chatbot-ui/src/components/MessageBubble/MessageBubble.tsx)**

```tsx
// Add in step rendering section
if (step.type === 'confirm-request') {
    return (
        <ConfirmButtons
            key={step.id}
            toolCallId={step.toolCallId}
            toolName={step.toolName}
            label={step.label}
            description={step.description}
            status={step.confirmStatus}
            onConfirm={handleConfirm}
            onReject={handleReject}
        />
    );
}
```

---

## Phase 8: Kafka Topics

**[create-topics.sh](infrastructure/docker/kafka/create-topics.sh)**
- Add topic: `agent.confirm` (for confirmation responses)

---

## User Experience Flow

```
1. User: "I need a safety inspection checklist for warehouses"

2. Agent discusses requirements (no tools called yet):
   - "What areas should the checklist cover?"
   - "How many sections do you need?"
   - "Any specific compliance requirements?"

3. User provides all details

4. Agent calls generate_checklist tool (CONFIRM_REQUIRED):
   Brain detects CONFIRM_REQUIRED → emits confirm_request to client

5. Frontend displays:
   ┌─────────────────────────────────────────┐
   │  🔧 Generate Checklist                  │
   │  Create 'Warehouse Safety' checklist    │
   │                                         │
   │  [ Confirm ]  [ Cancel ]                │
   └─────────────────────────────────────────┘

6a. User clicks "Confirm":
    → POST /confirm-response {confirmed: true}
    → Orchestrator dispatches to workers
    → Workers execute and return result
    → Checklist displayed in chat

6b. User clicks "Cancel":
    → POST /confirm-response {confirmed: false}
    → Orchestrator injects rejection result
    → Agent acknowledges cancellation
```

---

## Error Handling (Workers)

| Error | Handling |
|-------|----------|
| Tool not found | Return `{error: "tool_not_found", message: "..."}` |
| Plan access denied | Return `{error: "plan_access_denied", message: "..."}` |
| Tool not enabled | Return `{error: "tool_not_enabled", message: "..."}` (for USER_ENABLED tools) |
| Execution failed | Return `{error: "execution_failed", message: "..."}` |

The brain receives these errors as tool results and can respond appropriately to the user.

---

## Verification Plan

1. **Unit Tests:**
   - Asset loader functions
   - Tool catalog lookups
   - Worker validation logic (tool exists, plan access)

2. **Integration Tests:**
   - AUTO_EXECUTE flow: call → execute → result
   - USER_ENABLED flow (enabled): call → execute → result
   - USER_ENABLED flow (disabled): call → tool_not_enabled error
   - CONFIRM_REQUIRED flow: call → confirm_request → user confirms → execute → result
   - CONFIRM_REQUIRED rejection: call → confirm_request → user rejects → rejection result
   - Tool not found error handling
   - Plan access denied error handling

3. **Manual Testing:**
   - Start conversation, discuss checklist needs
   - Verify confirm buttons render
   - Click confirm, verify tool executes
   - Click cancel, verify agent acknowledges

---

## Implementation Order

1. **Phase 1** - Self-contained checklist generator (assets folder, update paths)
2. **Phase 2** - Tool catalog with ToolBehavior enum and plan features
3. **Phase 3** - Tool workers validation (exists, plan access, error handling)
4. **Phase 4** - Orchestrator confirm request emission
5. **Phase 5** - API Gateway confirm-response endpoint
6. **Phase 6** - Orchestrator confirm handler
7. **Phase 7** - Frontend ConfirmButtons component
8. **Phase 8** - Kafka topic setup and integration testing

---

## Critical Files Summary

| Purpose | File Path |
|---------|-----------|
| Tool catalog (single source of truth) | `libs/common/tool_catalog.py` |
| Tool base class | `services/tool-workers/src/tools/base.py` |
| Checklist generator | `services/tool-workers/src/tools/checklist_generator.py` |
| Tool workers main (validation) | `services/tool-workers/src/main.py` |
| Tool handler (orchestrator) | `services/orchestrator/src/handlers/tool_handler.py` |
| Confirm handler (orchestrator) | `services/orchestrator/src/handlers/confirm_handler.py` |
| Chat router (API) | `services/api-gateway/src/routers/chat.py` |
| SSE client (frontend) | `frontend/apps/demo/src/api/RealChatClient.ts` |
| Confirm buttons component | `frontend/packages/chatbot-ui/src/components/ConfirmButtons/ConfirmButtons.tsx` |
| Message rendering | `frontend/packages/chatbot-ui/src/components/MessageBubble/MessageBubble.tsx` |
