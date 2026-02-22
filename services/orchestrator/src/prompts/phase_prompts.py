"""Prompts for multi-phase agent execution.

Each prompt is designed for structured JSON output via complete_structured().
"""

TRIAGE_PROMPT = """\
You are a task classifier. Analyze the user's request and classify it.

Respond with JSON only. No markdown, no explanation outside the JSON.

Classification rules:
- "simple": Greetings, single factual questions ("What is X?"), simple conversational \
replies, or tasks needing exactly one tool call with a straightforward answer.
- "multi_phase": ANYTHING that benefits from multiple searches, multiple perspectives, \
or structured research. This includes:
  * Requests for comprehensive content (checklists, guides, reports, comparisons)
  * Questions spanning multiple topics or categories
  * Tasks where a single search cannot cover all aspects
  * Requests using words like "full", "comprehensive", "complete", "detailed", \
"everything", "all aspects", "in-depth", "thorough"
  * Comparison tasks ("compare X vs Y")
  * Research tasks ("research X", "find out about X")

Effort level: {effort_level}
Since effort is HIGH, you MUST classify as "multi_phase" unless the request is \
trivially simple (a greeting, a single factual lookup, or a yes/no question). \
When in doubt, always choose "multi_phase".

Examples:
- "Hi" → simple
- "What's the capital of France?" → simple
- "Build a checklist for inspecting a car" → multi_phase (covers engine, body, \
electrical, brakes — multiple categories need separate research)
- "Compare React vs Vue" → multi_phase (needs research on both, then comparison)
- "Explain how DNS works" → multi_phase with HIGH effort (multiple angles to cover)
- "Search for weather today" → simple

Available tools: {tool_names}

Output format:
{{
  "mode": "simple" | "multi_phase",
  "complexity_reason": "Brief explanation of why this classification",
  "needs_clarification": true | false,
  "clarification_question": "Question to ask user, if needs_clarification is true"
}}
"""

DECOMPOSE_PROMPT = """\
You are a task planner. Break down the user's request into sub-tasks that can be \
executed in parallel where possible.

Available tools: {tool_names}
Tool descriptions: {tool_descriptions}

Rules:
- Create specific, actionable sub-tasks that each cover a DIFFERENT aspect of the topic
- Use "tool_call" strategy when a tool can directly answer the sub-question
- Use "llm_call" strategy when analysis/reasoning is needed without tools
- Group independent sub-tasks together in execution_order for parallel execution
- Dependent sub-tasks go in later groups
- Generate a high-level task plan (todo list) that maps to these sub-tasks
- Each task in the plan should be a user-readable summary of a group of related work

CRITICAL — Diversity and deduplication:
- Every tool_call sub-task MUST have UNIQUE tool_arguments. NEVER create two sub-tasks \
that call the same tool with identical or near-identical parameters.
- For web_search, each query MUST target a DIFFERENT angle/category/aspect of the topic.
- BAD example: 5 searches all with "car inspection checklist" — this wastes resources
- GOOD example for "car inspection checklist":
  * "engine inspection checklist compression timing belts"
  * "brake system inspection disc pads fluid check"
  * "car electrical system battery alternator diagnostics"
  * "car body rust undercarriage suspension check"
  * "transmission fluid levels gearbox inspection"

Respond with JSON only:
{{
  "sub_tasks": [
    {{
      "id": "st-1",
      "description": "What this sub-task accomplishes",
      "strategy": "tool_call" | "llm_call",
      "tool_name": "web_search",
      "tool_arguments": {{"query": "specific search query for THIS specific angle"}},
      "llm_prompt": null
    }},
    {{
      "id": "st-2",
      "description": "Analyze X based on gathered data",
      "strategy": "llm_call",
      "tool_name": null,
      "tool_arguments": null,
      "llm_prompt": "Analyze the following data and provide insights on..."
    }}
  ],
  "execution_order": [
    ["st-1", "st-3"],
    ["st-2"]
  ],
  "task_plan": [
    {{
      "id": "t-1",
      "title": "Research topic X",
      "sub_task_ids": ["st-1", "st-3"]
    }},
    {{
      "id": "t-2",
      "title": "Analyze findings",
      "sub_task_ids": ["st-2"]
    }}
  ],
  "synthesis_guidance": "Instructions for how to combine results into final response"
}}
"""

SYNTHESIZE_PROMPT = """\
You are synthesizing research results into a comprehensive response.

Original question: {user_question}

Sub-task results:
{sub_task_results}

Synthesis guidance: {synthesis_guidance}

Instructions:
- Combine all sub-task results into a single, coherent, well-structured response
- Cross-reference information from different sources
- Resolve any contradictions by noting them
- Organize logically with clear sections
- Be comprehensive but concise
- Do NOT add information that wasn't in the sub-task results
- If sub-tasks failed, note what information is missing

Write the full response now. Do not include meta-commentary about the synthesis process.
"""

EVALUATE_PROMPT = """\
You are evaluating a draft response for quality and completeness.

Original question: {user_question}

Sub-tasks that were executed:
{sub_task_summaries}

Draft response:
{draft_response}

Evaluate on these criteria:
1. Completeness: Does it address all aspects of the question?
2. Accuracy: Are claims supported by the gathered evidence?
3. Coherence: Is it well-organized and clearly written?
4. Depth: Is the analysis sufficiently thorough?

Respond with JSON only:
{{
  "score": 1-10,
  "pass": true | false,
  "reasoning": "Brief explanation of the score",
  "gaps": ["list of identified gaps or missing aspects"],
  "suggested_actions": [
    {{
      "type": "additional_search" | "deeper_analysis" | "restructure" | "ask_user",
      "description": "What to do to address the gap",
      "tool_name": "web_search",
      "tool_arguments": {{"query": "..."}}
    }}
  ]
}}

Score thresholds: A score of {pass_score} or higher passes.
Be critical but fair. Only suggest actions for genuine gaps, not minor polish.
"""

REFLECT_PROMPT = """\
You are reviewing progress between phases of a multi-step task.

Original question: {user_question}

Current task plan:
{task_plan_json}

Phase just completed: {completed_phase}
Proposed next phase: {proposed_next_phase}

Results gathered so far:
{results_summary}

Review what was accomplished and decide the next action.

Respond with JSON only:
{{
  "next_action": "proceed" | "adjust" | "ask_user",
  "reasoning": "Brief explanation of your decision",
  "task_updates": [
    {{
      "task_id": "t-1",
      "status": "completed" | "in_progress" | "adjusted" | "blocked",
      "notes": "What was accomplished or learned"
    }}
  ],
  "adjustments": [
    {{
      "action": "add" | "remove" | "reorder",
      "task": {{"id": "t-new", "title": "New task description"}},
      "reason": "Why this change is needed"
    }}
  ],
  "question": "Question to ask the user, if next_action is ask_user",
  "question_context": "Context to help the user understand why this question matters"
}}

Rules:
- Choose "proceed" if the completed phase achieved its goals and the plan is on track
- Choose "adjust" if results suggest the plan needs modification (new sub-tasks, \
different approach, skip unnecessary work)
- Choose "ask_user" only when genuinely ambiguous input is needed that cannot be \
resolved from available information
- Keep adjustments minimal and focused
- Always update task statuses to reflect what actually happened
"""
