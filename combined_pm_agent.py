# Combined Python file
# Source directory: c:\Users\gusta\master\pm\src\pm_agent
# Files included (in order):
# - __init__.py
# - agent.py
# - analysis.py
# - analytics.py
# - cli.py
# - reporting.py
# - schema.py
# - tools.py
# - viz.py

# ---------- FILE: __init__.py ----------
"""CLI Project Manager Agent powered by LangGraph."""

__version__ = "0.1.0"

# ---------- FILE: agent.py ----------
"""LangGraph agent implementation for the PM Agent."""

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph

from .tools import TOOLS, get_project_root

# Load environment variables from .env file
# Look for .env in the package root directory (where pyproject.toml is)
package_root = Path(__file__).parent.parent.parent
env_path = package_root / ".env"
load_dotenv(env_path)


SYSTEM_PROMPT = """You are a Project Manager Agent operating on the local file system.

Your responsibilities:
- Help users plan and track their projects
- Maintain the project plan in 01_plan.json
- Log important decisions
- Create and update tasks based on user requests
- Generate Gantt charts automatically when tasks change

Key Rules:
1. The source of truth is 01_plan.json - always read it before making changes
2. NEVER output a Gantt chart directly in text - use the generate_gantt tool
3. When planning, check the Charter (00_project_charter.md) first to understand goals
4. Always confirm with the user before making significant changes
5. Use the tools provided - don't try to manipulate files directly
6. Be concise and professional in your responses
7. When adding tasks, ask for clarification on assignees and due dates if not provided

Available Information:
- Use read_context() to get an overview of the project
- Use read_plan() to see the complete task list
- Use add_task() to create new tasks
- Use update_task() to modify existing tasks
- Use append_log() to record important decisions
- Use write_file() to create documentation

Remember: You're helping manage real projects. Be accurate, thoughtful, and helpful.
"""


def create_agent(
    model: Optional[str] = None,
    temperature: float = 0.1,
    *,
    api_key: Optional[str] = None,
):
    """Create the LangGraph agent.

    Args:
        model: The Gemini model to use (default: gemini-2.5-flash)
        temperature: Temperature for the model (default: 0.1 for consistency)
        api_key: Optional Google AI Studio API key override

    Returns:
        Compiled LangGraph agent
    """
    model_name = model or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    client_kwargs = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    client = genai.Client(**client_kwargs)

    def _sanitize_schema_dict(schema):
        if isinstance(schema, dict):
            cleaned = {}
            for key, value in schema.items():
                if key == "default":
                    continue
                cleaned[key] = _sanitize_schema_dict(value)

            schema_type = cleaned.get("type")
            if schema_type == "null":
                cleaned.pop("type", None)
                cleaned["nullable"] = True
            if isinstance(schema_type, list):
                non_null = [t for t in schema_type if t != "null"]
                if "null" in schema_type:
                    cleaned["nullable"] = True
                cleaned["type"] = non_null[0] if non_null else schema_type[0]

            any_of = cleaned.get("anyOf")
            if isinstance(any_of, list):
                normalized = []
                nullable = cleaned.get("nullable", False)
                for item in any_of:
                    if isinstance(item, dict) and item.get("type") == "null":
                        nullable = True
                    else:
                        normalized.append(item)
                if normalized:
                    cleaned["anyOf"] = normalized
                else:
                    cleaned.pop("anyOf", None)
                if nullable:
                    cleaned["nullable"] = True
            return cleaned
        if isinstance(schema, list):
            return [
                _sanitize_schema_dict(item) if isinstance(item, dict) else item
                for item in schema
            ]
        return schema

    tool_declarations: list[genai_types.FunctionDeclaration] = []
    for tool in TOOLS:
        schema_dict = tool.args_schema.model_json_schema()
        sanitized = _sanitize_schema_dict(schema_dict)
        parameters = None
        if sanitized.get("properties"):
            parameters = genai_types.Schema(**sanitized)
        tool_declarations.append(
            genai_types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=parameters,
            )
        )

    tools_config = (
        [genai_types.Tool(function_declarations=tool_declarations)]
        if tool_declarations
        else None
    )

    base_config_kwargs: dict[str, object] = {
        "temperature": temperature,
    }
    if tools_config:
        base_config_kwargs["tools"] = tools_config

    def _build_config(system_instruction: Optional[str]) -> genai_types.GenerateContentConfig:
        kwargs = base_config_kwargs.copy()
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        return genai_types.GenerateContentConfig(**kwargs)

    def _stringify_content(content) -> str:
        """Convert Gemini content parts into a plain string."""
        if isinstance(content, str):
            return content

        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                else:
                    parts.append(str(item))
        elif content is not None:
            parts.append(str(content))

        return "\n\n".join(part for part in parts if part) if parts else ""

    def _ensure_text_message(message: BaseMessage) -> BaseMessage:
        """Make sure every message has string content."""
        new_content = _stringify_content(message.content)
        if new_content == message.content:
            return message
        if hasattr(message, "model_copy"):
            return message.model_copy(update={"content": new_content})
        data = message.__dict__.copy()
        data["content"] = new_content
        return message.__class__(**data)

    def _gather_system_instruction(messages: list[BaseMessage]) -> str:
        prompts = [SYSTEM_PROMPT.strip()]
        for msg in messages:
            if isinstance(msg, SystemMessage):
                text = _stringify_content(msg.content).strip()
                if text:
                    prompts.append(text)
        return "\n\n".join(p for p in prompts if p)

    def _tool_payload_from_text(text: str):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"result": text}

    def _message_to_content(message: BaseMessage):
        if isinstance(message, SystemMessage):
            return None

        text = _stringify_content(message.content)
        if isinstance(message, ToolMessage):
            name = getattr(message, "name", None) or "tool_response"
            payload = _tool_payload_from_text(text)
            part = genai_types.Part.from_function_response(
                name=name,
                response=payload,
            )
            return genai_types.Content(role="user", parts=[part])

        role = "user" if isinstance(message, HumanMessage) else "model"
        return genai_types.Content(
            role=role,
            parts=[genai_types.Part(text=text or "...")],
        )

    # Define the agent reasoning node
    def call_model(state: MessagesState):
        """Call the Gemini model with the current conversation."""
        messages = [_ensure_text_message(msg) for msg in state["messages"]]
        system_instruction = _gather_system_instruction(messages)
        contents = []
        for message in messages:
            content = _message_to_content(message)
            if content:
                contents.append(content)
        if not contents:
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text="Hello!")],
                )
            )

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=_build_config(system_instruction),
            )
        except Exception as exc:
            error_message = (
                "I hit an error while contacting Gemini. "
                f"Details: {exc}"
            )
            return {"messages": [AIMessage(content=error_message)]}

        tool_calls = []
        raw_tool_calls = []
        ai_text_parts = []
        if response and response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.function_call:
                        call_id = part.function_call.id or f"call_{uuid.uuid4().hex}"
                        args = part.function_call.args or {}
                        tool_calls.append(
                            {
                                "id": call_id,
                                "name": part.function_call.name,
                                "args": args,
                            }
                        )
                        raw_tool_calls.append(
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": part.function_call.name,
                                    "arguments": json.dumps(args),
                                },
                            }
                        )
                    if part.text:
                        ai_text_parts.append(part.text)

        ai_text = "\n\n".join(part.strip() for part in ai_text_parts if part).strip()
        additional_kwargs = {}
        if raw_tool_calls:
            additional_kwargs["tool_calls"] = raw_tool_calls

        message_kwargs = {
            "content": ai_text or "",
            "additional_kwargs": additional_kwargs,
        }
        if tool_calls:
            message_kwargs["tool_calls"] = tool_calls

        ai_message = AIMessage(**message_kwargs)
        return {"messages": [ai_message]}

    def _extract_tool_calls(message):
        """Return tool call entries from an AI message."""
        calls = getattr(message, "tool_calls", None)
        if not calls:
            calls = message.additional_kwargs.get("tool_calls")
        return calls or []

    def call_tools(state: MessagesState):
        """Execute the requested tools."""
        messages = state["messages"]
        last_message = messages[-1]

        tool_calls = _extract_tool_calls(last_message)
        tool_results = []

        for tool_call in tool_calls:
            try:
                tool_call_id = getattr(tool_call, "id", None) or tool_call.get("id")
                tool_name = (
                    getattr(tool_call, "name", None)
                    or getattr(getattr(tool_call, "function", None), "name", None)
                    or tool_call.get("name")
                    or (tool_call.get("function", {}) or {}).get("name")
                )
                tool_args = (
                    getattr(tool_call, "args", None)
                    or getattr(getattr(tool_call, "function", None), "arguments", None)
                    or tool_call.get("args")
                    or (tool_call.get("function", {}) or {}).get("arguments")
                    or {}
                )
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except Exception:
                        tool_args = {"input": tool_args}

                tool = next((t for t in TOOLS if t.name == tool_name), None)
                if not tool:
                    tool_results.append(
                        ToolMessage(
                            content=f"Error: Unknown tool '{tool_name}'",
                            tool_call_id=tool_call_id,
                            name=tool_name or "unknown",
                        )
                    )
                    continue

                try:
                    result = tool.invoke(tool_args)
                    tool_results.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call_id,
                            name=tool_name,
                        )
                    )
                except Exception as e:
                    import traceback

                    error_msg = (
                        f"Error executing {tool_name}: {str(e)}\n{traceback.format_exc()}"
                    )
                    print(f"Tool execution error: {error_msg}")
                    tool_results.append(
                        ToolMessage(
                            content=f"Error: {str(e)}",
                            tool_call_id=tool_call_id,
                            name=tool_name,
                        )
                    )
            except Exception as e:
                import traceback

                error_msg = f"Error processing tool call: {str(e)}\n{traceback.format_exc()}"
                print(f"Tool call processing error: {error_msg}")
                try:
                    tool_call_id = getattr(tool_call, "id", None) or tool_call.get("id", "unknown")
                    tool_results.append(
                        ToolMessage(
                            content=f"Error processing tool call: {str(e)}",
                            tool_call_id=tool_call_id,
                            name=getattr(tool_call, "name", None) or tool_call.get("name", "unknown"),
                        )
                    )
                except Exception:
                    pass

        if len(tool_results) != len(tool_calls):
            print(
                f"WARNING: Tool call count mismatch. Calls: {len(tool_calls)}, Results: {len(tool_results)}"
            )

        return {"messages": tool_results}

    def should_continue(state: MessagesState) -> Literal["tools", "__end__"]:
        """Route to tools or end based on last message."""
        messages = state["messages"]
        last_message = messages[-1]

        tool_calls = getattr(last_message, "tool_calls", None)
        if not tool_calls:
            tool_calls = last_message.additional_kwargs.get("tool_calls")

        if tool_calls:
            return "tools"
        return "__end__"

    # Build the graph
    workflow = StateGraph(MessagesState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", call_tools)

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")

    # Add memory/checkpointer
    root = get_project_root()
    pm_dir = root / ".pm"
    pm_dir.mkdir(exist_ok=True)

    memory_path = pm_dir / "state.sqlite"

    # Create a persistent SQLite connection for the checkpointer
    conn = sqlite3.connect(str(memory_path), check_same_thread=False)
    memory = SqliteSaver(conn)

    # Compile
    app = workflow.compile(checkpointer=memory)
    return app


def run_agent(query: str, thread_id: str = "main") -> str:
    """Run the agent with a single query.

    Args:
        query: The user's question or command
        thread_id: Thread identifier for conversation memory

    Returns:
        The agent's response
    """
    agent = create_agent()

    config = {"configurable": {"thread_id": thread_id}}

    # Run the agent
    response = agent.invoke(
        {"messages": [HumanMessage(content=query)]},
        config=config
    )

    # Extract the final message
    final_message = response["messages"][-1]

    return final_message.content

# ---------- FILE: analysis.py ----------
"""Advanced analytics helpers for the PM Agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .schema import FocusBoard, FocusItem, ProjectPlan, Task


@dataclass
class CriticalPathTask:
    """Represents scheduling data for a single task."""

    task_id: int
    title: str
    duration: int
    earliest_start: date
    earliest_finish: date
    latest_start: date
    latest_finish: date
    slack: int
    is_critical: bool


@dataclass
class CriticalPathResult:
    """Aggregate critical path analysis."""

    tasks: List[CriticalPathTask]
    total_duration: int
    project_start: date
    warnings: List[str]


@dataclass
class RiskDriver:
    """Information about a task that strongly influences schedule risk."""

    task_id: int
    title: str
    correlation: float
    avg_duration: float


@dataclass
class MonteCarloResult:
    """Monte Carlo schedule simulation summary."""

    iterations: int
    finish_percentiles: Dict[str, date]
    duration_percentiles: Dict[str, float]
    risk_drivers: List[RiskDriver]
    project_start: date
    cost_percentiles: Optional[Dict[str, float]] = None
    probability_on_time: Optional[float] = None
    milestone_probability: Optional[float] = None
    milestone_label: Optional[str] = None
    effort_percentiles: Optional[Dict[str, float]] = None
    finish_samples: Optional[np.ndarray] = None
    effort_samples: Optional[np.ndarray] = None
    cost_samples: Optional[np.ndarray] = None


@dataclass
class ArtifactInfo:
    """Metadata about files stored under artifacts/."""

    path: str
    size_kb: float
    modified: datetime


def _project_start_date(plan: ProjectPlan) -> date:
    dates = []
    for task in plan.tasks:
        if task.start_date:
            dates.append(task.start_date)
        if task.due_date:
            dates.append(task.due_date)
    return min(dates) if dates else date.today()


def _duration_from_dates(task: Task) -> Optional[int]:
    if task.start_date and task.due_date:
        return max(1, (task.due_date - task.start_date).days or 1)
    if task.due_date:
        # Assume at least one day if only a due date exists
        return 1
    return None


def _estimated_duration(task: Task) -> int:
    duration = _duration_from_dates(task)
    if duration:
        return duration
    if task.likely_days:
        return max(1, task.likely_days)
    if task.optimistic_days and task.pessimistic_days:
        return max(1, int(round((task.optimistic_days + task.pessimistic_days) / 2)))
    return 5


def _duration_estimates_for_risk(task: Task) -> tuple[int, int, int]:
    likely = task.likely_days or _duration_from_dates(task) or 5
    optimistic = task.optimistic_days or max(1, int(round(likely * 0.7)))
    pessimistic = task.pessimistic_days or max(likely + 1, int(round(likely * 1.5)))

    optimistic = max(1, min(optimistic, likely))
    pessimistic = max(likely + 1, pessimistic)

    return optimistic, max(1, likely), pessimistic


def compute_critical_path(plan: ProjectPlan) -> CriticalPathResult:
    """Compute earliest/latest dates and slack for each task."""
    tasks = plan.tasks
    project_start = _project_start_date(plan)
    warnings: List[str] = []

    if not tasks:
        return CriticalPathResult([], 0, project_start, ["No tasks defined"])

    tasks_by_id: Dict[int, Task] = {task.id: task for task in tasks}
    durations = {task.id: _estimated_duration(task) for task in tasks}

    successors: Dict[int, List[int]] = {task.id: [] for task in tasks}
    indegree: Dict[int, int] = {task.id: 0 for task in tasks}

    for task in tasks:
        for dep in task.dependencies:
            if dep not in successors:
                warnings.append(f"Task #{task.id} references missing dependency #{dep}.")
                continue
            successors[dep].append(task.id)
            indegree[task.id] += 1

    # Kahn topological sort
    order: List[int] = []
    queue = [task_id for task_id, deg in indegree.items() if deg == 0]

    while queue:
        current = queue.pop(0)
        order.append(current)
        for neighbor in successors[current]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(tasks):
        warnings.append("Dependency graph may contain a cycle; results could be inaccurate.")

    earliest_start: Dict[int, int] = {task_id: 0 for task_id in tasks_by_id}
    earliest_finish: Dict[int, int] = {task_id: durations[task_id] for task_id in tasks_by_id}

    for task_id in order:
        deps = tasks_by_id[task_id].dependencies
        if deps:
            valid_deps = [earliest_finish.get(dep, 0) for dep in deps]
            start_offset = max(valid_deps) if valid_deps else 0
        else:
            start_offset = 0

        earliest_start[task_id] = start_offset
        earliest_finish[task_id] = start_offset + durations[task_id]

    total_duration = max(earliest_finish.values()) if earliest_finish else 0

    latest_finish: Dict[int, int] = {task_id: total_duration for task_id in tasks_by_id}
    latest_start: Dict[int, int] = {
        task_id: total_duration - durations[task_id] for task_id in tasks_by_id
    }

    for task_id in reversed(order):
        succs = successors.get(task_id, [])
        if succs:
            min_latest_start = min(latest_start[s] for s in succs)
            latest_finish[task_id] = min_latest_start
            latest_start[task_id] = min_latest_start - durations[task_id]

    schedule: List[CriticalPathTask] = []
    for task_id in order:
        task = tasks_by_id[task_id]
        es_days = earliest_start[task_id]
        ef_days = earliest_finish[task_id]
        ls_days = latest_start[task_id]
        lf_days = latest_finish[task_id]
        slack = max(0, ls_days - es_days)

        schedule.append(
            CriticalPathTask(
                task_id=task_id,
                title=task.title,
                duration=durations[task_id],
                earliest_start=project_start + timedelta(days=es_days),
                earliest_finish=project_start + timedelta(days=ef_days),
                latest_start=project_start + timedelta(days=ls_days),
                latest_finish=project_start + timedelta(days=lf_days),
                slack=slack,
                is_critical=slack == 0,
            )
        )

    schedule.sort(key=lambda item: item.earliest_start)
    return CriticalPathResult(schedule, total_duration, project_start, warnings)


def run_monte_carlo_simulation(
    plan: ProjectPlan,
    iterations: int = 2000,
    cost_rate: Optional[float] = None,
    deadline: Optional[date] = None,
    milestone_deadline: Optional[date] = None,
    shock_days: int = 0,
    milestone_label: Optional[str] = None
) -> MonteCarloResult:
    """Run a Monte Carlo simulation to understand schedule risk."""
    tasks = plan.tasks
    project_start = _project_start_date(plan)

    if not tasks:
        today = date.today()
        finish = {"p10": today, "p50": today, "p90": today}
        duration = {"p10": 0.0, "p50": 0.0, "p90": 0.0}
        zero_array = np.zeros(iterations)
        return MonteCarloResult(
            iterations=iterations,
            finish_percentiles=finish,
            duration_percentiles=duration,
            risk_drivers=[],
            project_start=project_start,
            cost_percentiles=None,
            probability_on_time=None,
            milestone_probability=None,
            milestone_label=milestone_label,
            effort_percentiles={"p10": 0.0, "p50": 0.0, "p90": 0.0},
            finish_samples=zero_array,
            effort_samples=zero_array.copy(),
            cost_samples=None,
        )

    tasks_by_id = {task.id: task for task in tasks}
    durations = {task.id: _duration_estimates_for_risk(task) for task in tasks}

    successors: Dict[int, List[int]] = {task.id: [] for task in tasks}
    indegree: Dict[int, int] = {task.id: 0 for task in tasks}

    for task in tasks:
        for dep in task.dependencies:
            if dep in successors:
                successors[dep].append(task.id)
                indegree[task.id] += 1

    order: List[int] = []
    queue = [task_id for task_id, deg in indegree.items() if deg == 0]
    while queue:
        current = queue.pop(0)
        order.append(current)
        for neighbor in successors[current]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    # Fallback: if graph not connected, add remaining tasks
    for task_id in tasks_by_id:
        if task_id not in order:
            order.append(task_id)

    finish_samples: Dict[int, np.ndarray] = {}
    duration_samples: Dict[int, np.ndarray] = {}

    for task_id in order:
        optimistic, likely, pessimistic = durations[task_id]
        samples = np.random.triangular(optimistic, likely, pessimistic, size=iterations)
        samples = np.clip(np.round(samples), 1, None)
        duration_samples[task_id] = samples

        start_samples = np.zeros(iterations)
        for dep in tasks_by_id[task_id].dependencies:
            if dep in finish_samples:
                start_samples = np.maximum(start_samples, finish_samples[dep])
        if shock_days > 0 and tasks_by_id[task_id].dependencies:
            extra_delay = np.random.uniform(0, shock_days, size=iterations)
        else:
            extra_delay = 0
        finish_samples[task_id] = start_samples + samples + extra_delay

    project_finish = np.max(np.vstack(list(finish_samples.values())), axis=0)

    percentiles_days = np.percentile(project_finish, [10, 50, 90])
    finish_percentiles = {
        "p10": project_start + timedelta(days=int(percentiles_days[0])),
        "p50": project_start + timedelta(days=int(percentiles_days[1])),
        "p90": project_start + timedelta(days=int(percentiles_days[2])),
    }
    duration_percentiles = {
        "p10": float(percentiles_days[0]),
        "p50": float(percentiles_days[1]),
        "p90": float(percentiles_days[2]),
    }

    risk_drivers: List[RiskDriver] = []
    finish_array = project_finish

    for task_id, samples in finish_samples.items():
        if np.std(samples) == 0:
            continue
        corr_matrix = np.corrcoef(samples, finish_array)
        corr = corr_matrix[0, 1]
        if np.isnan(corr):
            continue
        risk_drivers.append(
            RiskDriver(
                task_id=task_id,
                title=tasks_by_id[task_id].title,
                correlation=float(corr),
                avg_duration=float(np.mean(duration_samples[task_id])),
            )
        )

    risk_drivers.sort(key=lambda driver: abs(driver.correlation), reverse=True)

    total_person_days = np.sum(np.vstack(list(duration_samples.values())), axis=0)
    effort_percentiles = {
        "p10": float(np.percentile(total_person_days, 10)),
        "p50": float(np.percentile(total_person_days, 50)),
        "p90": float(np.percentile(total_person_days, 90)),
    }

    cost_percentiles = None
    cost_samples = None
    if cost_rate:
        cost_samples = total_person_days * cost_rate
        cost_percentiles = {
            "p10": float(np.percentile(cost_samples, 10)),
            "p50": float(np.percentile(cost_samples, 50)),
            "p90": float(np.percentile(cost_samples, 90)),
        }

    probability_on_time = None
    if deadline:
        horizon_days = (deadline - project_start).days
        probability_on_time = float(np.mean(project_finish <= horizon_days))

    milestone_probability = None
    if milestone_deadline:
        milestone_tasks = [
            task_id for task_id, task in tasks_by_id.items()
            if task.due_date and task.due_date <= milestone_deadline
        ]
        if milestone_tasks:
            milestone_finish = np.max(
                np.vstack([finish_samples[task_id] for task_id in milestone_tasks]),
                axis=0
            )
            milestone_horizon = (milestone_deadline - project_start).days
            milestone_probability = float(np.mean(milestone_finish <= milestone_horizon))

    return MonteCarloResult(
        iterations=iterations,
        finish_percentiles=finish_percentiles,
        duration_percentiles=duration_percentiles,
        risk_drivers=risk_drivers,
        project_start=project_start,
        cost_percentiles=cost_percentiles,
        probability_on_time=probability_on_time,
        milestone_probability=milestone_probability,
        milestone_label=milestone_label,
        effort_percentiles=effort_percentiles,
        finish_samples=project_finish,
        effort_samples=total_person_days,
        cost_samples=cost_samples,
    )


def summarize_monte_carlo(result: MonteCarloResult) -> Dict[str, object]:
    """Return a JSON-ready summary of Monte Carlo outcomes."""

    def _format_percentiles(percentiles: Dict[str, date]) -> Dict[str, str]:
        return {key: value.isoformat() for key, value in percentiles.items()}

    finish_stats: Dict[str, float] = {}
    if result.finish_samples is not None and len(result.finish_samples) > 0:
        finish_stats = {
            "mean_days": float(np.mean(result.finish_samples)),
            "std_days": float(np.std(result.finish_samples)),
            "min_days": float(np.min(result.finish_samples)),
            "max_days": float(np.max(result.finish_samples)),
        }

    cost_stats: Optional[Dict[str, float]] = None
    if result.cost_samples is not None and len(result.cost_samples) > 0:
        cost_stats = {
            "mean": float(np.mean(result.cost_samples)),
            "std": float(np.std(result.cost_samples)),
            "min": float(np.min(result.cost_samples)),
            "max": float(np.max(result.cost_samples)),
        }

    summary: Dict[str, object] = {
        "iterations": result.iterations,
        "generated_at": datetime.utcnow().isoformat(),
        "project_start": result.project_start.isoformat(),
        "finish_percentiles": _format_percentiles(result.finish_percentiles),
        "duration_percentiles": result.duration_percentiles,
        "effort_percentiles": result.effort_percentiles,
        "finish_distribution_stats": finish_stats,
        "probability_on_time": result.probability_on_time,
        "milestone_probability": result.milestone_probability,
        "milestone_label": result.milestone_label,
        "risk_drivers": [
            {
                "task_id": driver.task_id,
                "title": driver.title,
                "correlation": driver.correlation,
                "avg_duration": driver.avg_duration,
            }
            for driver in result.risk_drivers
        ],
    }

    if result.cost_percentiles:
        summary["cost_percentiles"] = result.cost_percentiles
    if cost_stats:
        summary["cost_distribution_stats"] = cost_stats

    return summary


def build_focus_board(
    plan: ProjectPlan,
    horizon_days: int = 14,
    limit: int = 10,
) -> FocusBoard:
    """Create a prioritized list of near-term or blocked tasks."""
    today = date.today()
    items: List[FocusItem] = []

    reason_priority = {"overdue": 0, "blocked": 1, "priority_gap": 2, "due_soon": 3}

    for task in plan.tasks:
        reason: Optional[str] = None
        days_remaining: Optional[int] = None

        if task.due_date:
            days_remaining = (task.due_date - today).days
            if days_remaining < 0:
                reason = "overdue"
            elif days_remaining <= horizon_days:
                reason = "due_soon"
        if task.status == "blocked":
            reason = "blocked"
        priority_flag = False
        if task.priority:
            priority_flag = task.priority.lower() in {"p0", "p1", "critical", "high"}
        if not reason and priority_flag and not task.assignee:
            reason = "priority_gap"

        if reason:
            items.append(
                FocusItem(
                    task_id=task.id,
                    title=task.title,
                    due_date=task.due_date,
                    status=task.status,
                    assignee=task.assignee,
                    days_remaining=days_remaining,
                    reason=reason,
                )
            )

    items.sort(
        key=lambda item: (
            reason_priority.get(item.reason, 99),
            item.days_remaining if item.days_remaining is not None else 999,
        )
    )

    if limit:
        items = items[:limit]

    return FocusBoard(
        generated_at=datetime.utcnow(),
        horizon_days=horizon_days,
        max_items=limit,
        items=items,
    )


def summarize_artifacts(root: Path, max_items: int = 20) -> List[ArtifactInfo]:
    """Return up to `max_items` recent artifact files."""
    artifacts_dir = root / "artifacts"
    if not artifacts_dir.exists():
        return []

    artifacts: List[ArtifactInfo] = []
    for path in artifacts_dir.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        artifacts.append(
            ArtifactInfo(
                path=str(path.relative_to(root)).replace("\\", "/"),
                size_kb=stat.st_size / 1024,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )
        )

    artifacts.sort(key=lambda art: art.modified, reverse=True)
    return artifacts[:max_items]

# ---------- FILE: analytics.py ----------
"""Project health analytics and time-series helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .schema import ProjectPlan
from .tools import get_project_root


@dataclass
class TaskStatusRecord:
    """Task status information captured in a snapshot."""

    task_id: int
    status: str
    due_date: Optional[date]
    title: str
    assignee: Optional[str]


@dataclass
class StatusSnapshot:
    """Recorded totals and per-task data at a given moment."""

    captured_at: datetime
    total_tasks: int
    status_counts: Dict[str, int]
    tasks: Dict[int, TaskStatusRecord]

    @classmethod
    def from_dict(cls, payload: Dict) -> "StatusSnapshot":
        captured_at = datetime.fromisoformat(payload["captured_at"])
        tasks: Dict[int, TaskStatusRecord] = {}
        for task in payload.get("tasks", []):
            due = date.fromisoformat(task["due_date"]) if task.get("due_date") else None
            tasks[task["id"]] = TaskStatusRecord(
                task_id=task["id"],
                status=task.get("status", "todo"),
                due_date=due,
                title=task.get("title", ""),
                assignee=task.get("assignee"),
            )
        total_tasks = payload.get("total_tasks", len(tasks))
        return cls(
            captured_at=captured_at,
            total_tasks=total_tasks,
            status_counts=payload.get("status_counts", {}),
            tasks=tasks,
        )


@dataclass
class TaskTimeline:
    """Derived lifecycle info for each task."""

    first_seen: Dict[int, datetime]
    completion_times: Dict[int, datetime]
    due_dates: Dict[int, date]
    titles: Dict[int, str]
    assignees: Dict[int, Optional[str]]


def _history_path() -> Path:
    root = get_project_root()
    history_dir = root / ".pm" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / "status_history.jsonl"


def record_status_snapshot(plan: ProjectPlan) -> StatusSnapshot:
    """Append the current plan state to the history log."""
    path = _history_path()
    now = datetime.utcnow()

    status_counts: Dict[str, int] = {}
    tasks_payload: List[Dict] = []
    for task in plan.tasks:
        status_counts[task.status] = status_counts.get(task.status, 0) + 1
        tasks_payload.append(
            {
                "id": task.id,
                "status": task.status,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "title": task.title,
                "assignee": task.assignee,
            }
        )

    payload = {
        "captured_at": now.isoformat(),
        "status_counts": status_counts,
        "total_tasks": len(plan.tasks),
        "tasks": tasks_payload,
    }

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")

    return StatusSnapshot.from_dict(payload)


def load_status_history(limit: Optional[int] = None) -> List[StatusSnapshot]:
    """Load all stored status history entries."""
    path = _history_path()
    if not path.exists():
        return []

    snapshots: List[StatusSnapshot] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshots.append(StatusSnapshot.from_dict(payload))

    snapshots.sort(key=lambda snap: snap.captured_at)
    if limit:
        snapshots = snapshots[-limit:]
    return snapshots


def filter_history_by_days(history: List[StatusSnapshot], days: int) -> List[StatusSnapshot]:
    """Return only history within the last `days` days."""
    if days <= 0:
        return history
    cutoff = datetime.utcnow() - timedelta(days=days)
    return [snap for snap in history if snap.captured_at >= cutoff]


def compute_burnup_series(history: List[StatusSnapshot]) -> List[Dict]:
    """Aggregate burnup/burndown friendly points by day."""
    daily: Dict[date, Dict] = {}
    for snapshot in history:
        day = snapshot.captured_at.date()
        done = snapshot.status_counts.get("done", 0)
        total = snapshot.total_tasks
        remaining = max(0, total - done)
        blocked = snapshot.status_counts.get("blocked", 0)
        daily[day] = {
            "date": day,
            "done": done,
            "total": total,
            "remaining": remaining,
            "blocked": blocked,
        }
    return [daily[day] for day in sorted(daily.keys())]


def derive_task_timelines(history: List[StatusSnapshot]) -> TaskTimeline:
    """Compute first-seen, completion, and due-date tracking for tasks."""
    first_seen: Dict[int, datetime] = {}
    completion_times: Dict[int, datetime] = {}
    due_dates: Dict[int, date] = {}
    titles: Dict[int, str] = {}
    assignees: Dict[int, Optional[str]] = {}

    for snapshot in history:
        for task_id, record in snapshot.tasks.items():
            if task_id not in first_seen:
                first_seen[task_id] = snapshot.captured_at
            if record.due_date:
                due_dates[task_id] = record.due_date
            if record.title:
                titles[task_id] = record.title
            if task_id not in assignees or record.assignee:
                assignees[task_id] = record.assignee
            if record.status == "done" and task_id not in completion_times:
                completion_times[task_id] = snapshot.captured_at

    return TaskTimeline(
        first_seen=first_seen,
        completion_times=completion_times,
        due_dates=due_dates,
        titles=titles,
        assignees=assignees,
    )


def compute_throughput_series(
    history: List[StatusSnapshot],
    timeline: Optional[TaskTimeline] = None,
) -> List[Dict]:
    """Return number of tasks completed per day."""
    if timeline is None:
        timeline = derive_task_timelines(history)

    counts: Dict[date, int] = {}
    for completed_at in timeline.completion_times.values():
        day = completed_at.date()
        counts[day] = counts.get(day, 0) + 1

    return [
        {"date": day, "completed": counts[day]}
        for day in sorted(counts.keys())
    ]


def compute_cycle_time_stats(
    history: List[StatusSnapshot],
    timeline: Optional[TaskTimeline] = None,
) -> Dict:
    """Return cycle-time samples and percentiles."""
    if timeline is None:
        timeline = derive_task_timelines(history)

    values: List[float] = []
    samples: List[Dict] = []
    for task_id, completed_at in timeline.completion_times.items():
        start = timeline.first_seen.get(task_id)
        if not start:
            continue
        days = max(0.0, (completed_at - start).days)
        values.append(days)
        samples.append(
            {
                "task_id": task_id,
                "title": timeline.titles.get(task_id, ""),
                "cycle_days": days,
                "completed_at": completed_at.date().isoformat(),
            }
        )

    percentiles: Dict[str, float] = {}
    mean = None
    if values:
        arr = np.array(values, dtype=float)
        percentiles = {
            "p50": float(np.percentile(arr, 50)),
            "p80": float(np.percentile(arr, 80)),
            "p90": float(np.percentile(arr, 90)),
        }
        mean = float(np.mean(arr))

    return {
        "count": len(values),
        "mean": mean,
        "percentiles": percentiles,
        "samples": sorted(samples, key=lambda item: item["cycle_days"], reverse=True),
    }


def compute_slip_stats(
    history: List[StatusSnapshot],
    timeline: Optional[TaskTimeline] = None,
) -> Dict:
    """Return planned vs actual completion deltas."""
    if timeline is None:
        timeline = derive_task_timelines(history)

    records: List[Dict] = []
    values: List[int] = []
    on_time = late = 0

    for task_id, completed_at in timeline.completion_times.items():
        due = timeline.due_dates.get(task_id)
        if not due:
            continue
        slip_days = (completed_at.date() - due).days
        values.append(slip_days)
        if slip_days > 0:
            late += 1
        else:
            on_time += 1
        records.append(
            {
                "task_id": task_id,
                "title": timeline.titles.get(task_id, ""),
                "slip_days": slip_days,
                "completed_at": completed_at.date().isoformat(),
                "due_date": due.isoformat(),
            }
        )

    percentiles: Dict[str, float] = {}
    if values:
        arr = np.array(values, dtype=float)
        percentiles = {
            "p10": float(np.percentile(arr, 10)),
            "p50": float(np.percentile(arr, 50)),
            "p90": float(np.percentile(arr, 90)),
        }

    return {
        "on_time": on_time,
        "late": late,
        "records": sorted(records, key=lambda item: item["slip_days"], reverse=True),
        "percentiles": percentiles,
    }

# ---------- FILE: cli.py ----------
"""CLI interface for the PM Agent using Typer."""

import os
import sys
import json
import subprocess
import time
import shlex
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime, date, timedelta

import typer
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.align import Align
from rich import print as rprint
from langchain_core.messages import HumanMessage
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML

from .agent import create_agent, run_agent
from .analysis import (
    build_focus_board,
    compute_critical_path,
    run_monte_carlo_simulation,
    summarize_artifacts,
    summarize_monte_carlo,
)
from .schema import Task
from .analytics import (
    record_status_snapshot,
    load_status_history,
    filter_history_by_days,
    compute_burnup_series,
    derive_task_timelines,
    compute_throughput_series,
    compute_cycle_time_stats,
    compute_slip_stats,
)
from .reporting import generate_documentation_report
from .tools import (
    load_plan,
    ensure_project_files,
    get_project_root,
    load_focus_board,
    save_focus_board,
    import_plan_from_csv,
    save_plan,
)
from .viz import (
    generate_gantt,
    render_text_gantt,
    save_finish_distribution_plot,
    save_risk_driver_plot,
    save_cost_distribution_plot,
    save_burnup_chart,
    save_throughput_chart,
    save_slip_histogram,
)

app = typer.Typer(help="CLI Project Manager Agent", invoke_without_command=True)
console = Console()

SLASH_COMMANDS = {
    "status": "/status              Show task table",
    "status --week": "/status --week       Get AI weekly summary",
    "plan": "/plan                View the plan JSON",
    "plan --edit": "/plan --edit         Edit plan in editor",
    "plan --update-from-csv": "/plan --update-from-csv  Import edits from plan.csv",
    "gantt": "/gantt               Display Gantt chart in CLI",
    "gantt --open": "/gantt --open        Display chart and open PNG",
    "followup": "/followup <task>     Quick add task",
    "criticalpath": "/criticalpath        Run critical path analysis",
    "risk": "/risk                Monte Carlo schedule risk",
    "forecast": "/forecast           Advanced Monte Carlo forecast",
    "health": "/health              Show health signals dashboard",
    "workload": "/workload           Prioritization + capacity dashboard",
    "focus": "/focus               Build/show near-term task list",
    "log": "/log \"...\"          Append entry to log",
    "documentation": "/documentation       Generate Word report",
    "artifacts": "/artifacts           List artifacts",
    "new": "/new <project>       Create new project",
    "archive": "/archive             Archive current project",
    "help": "/help                Show help",
    "exit": "/exit or /quit       Exit PM Agent",
}


class SlashCommandCompleter(Completer):
    """Provide slash-command completion hints inside the REPL."""

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return

        # Current token (without slash)
        token = text[1:].lower()

        # Propose commands that start with this token
        for command, summary in SLASH_COMMANDS.items():
            if command.startswith(token):
                yield Completion(
                    summary.split()[0],
                    start_position=-len(text),
                    display=summary,
                    style="class:completion",
                )

def show_help():
    """Display help information for slash commands."""
    console.print("\n[bold cyan]Control Tower Commands[/bold cyan]\n")

    console.print("[bold]Regular Messages[/bold]")
    console.print("Just type naturally to chat with the AI agent:")
    console.print("[dim]  > Add 3 tasks for the next sprint[/dim]\n")

    console.print("[bold]Slash Commands[/bold]")
    console.print("  /status              Show task table")
    console.print("  /status --week       Get AI weekly summary")
    console.print("  /plan                View the plan JSON")
    console.print("  /plan --edit         Edit plan in editor")
    console.print("  /plan --update-from-csv [file]  Import plan edits from CSV")
    console.print("  /gantt               Display Gantt chart in CLI + save PNG")
    console.print("  /gantt --open        Display chart + save PNG + open file")
    console.print("  /followup <task>     Quick add task")
    console.print("    [dim]Example: /followup \"Fix bug\" --due 2025-12-01 --assign john[/dim]")
    console.print("  /criticalpath        Run critical path analysis")
    console.print("  /risk                Run Monte Carlo schedule risk simulation")
    console.print("  /forecast            Advanced Monte Carlo forecast (costs, deadlines)")
    console.print("  /health              Show trend dashboard + export charts")
    console.print("  /workload            Priority + workload balancing dashboard")
    console.print("  /focus               Build/show near-term task list")
    console.print("  /log \"...\"          Append an entry to the decision log")
    console.print("  /documentation       Generate a Word report with artifacts")
    console.print("  /artifacts           List recent files in artifacts/")
    console.print("  /new <project>       Create new project")
    console.print("  /archive             Archive current project")
    console.print("  /help                Show this help")
    console.print("  /exit or /quit       Exit PM Agent\n")

    console.print("[bold]Tips[/bold]")
    console.print("  - Press Ctrl+C at any time to exit")
    console.print("  - All slash commands can also be used directly (e.g., pm status)\n")


def parse_date_option(date_str: str) -> date:
    """Parse date strings supporting ISO or common slash formats."""
    formats = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d")
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date format '{date_str}'. Use YYYY-MM-DD or MM/DD/YYYY.")


def handle_status_command(args: list):
    """Handle /status command."""
    week_mode = "--week" in args

    if week_mode:
        # Use agent to generate summary
        console.print("[cyan]Generating weekly summary...[/cyan]\n")
        response = run_agent("Summarize the project logs and plan progress for this week.")
        console.print(Markdown(response))
        return

    # Load plan
    plan = load_plan()

    if not plan.tasks:
        console.print("[yellow]No tasks in the project plan yet.[/yellow]")
        console.print("Try chatting with the agent to add tasks")
        return

    # Create status table
    table = Table(title="Project Status")

    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Assignee", style="green")
    table.add_column("Due Date", style="yellow")
    table.add_column("Priority", style="red")
    table.add_column("Effort", style="cyan", justify="right")

    # Status indicators
    status_emoji = {
        "todo": "[ ]",
        "in_progress": "[>]",
        "done": "[X]",
        "blocked": "[!]"
    }

    for task in plan.tasks:
        table.add_row(
            str(task.id),
            f"{status_emoji.get(task.status, '[ ]')} {task.status}",
            task.title,
            task.assignee or "Unassigned",
            task.due_date.isoformat() if task.due_date else "No due date",
            (task.priority or "-").title() if task.priority else "-",
            f"{task.effort_points:.1f}" if task.effort_points is not None else "-"
        )

    console.print(table)
    console.print(f"\nTotal tasks: {len(plan.tasks)}")

    snapshot = record_status_snapshot(plan)
    console.print(
        f"[dim]Captured trend snapshot at {snapshot.captured_at.isoformat()} UTC for health dashboards.[/dim]"
    )


def handle_plan_command(args: list):
    """Handle /plan command."""
    edit_mode = "--edit" in args
    plan_path = get_project_root() / "01_plan.json"

    # CSV import flag
    csv_path = None
    if "--csv-path" in args:
        csv_idx = args.index("--csv-path")
        if csv_idx + 1 < len(args):
            csv_path = args[csv_idx + 1]
    if "--update-from-csv" in args:
        idx = args.index("--update-from-csv")
        # optional path argument immediately after flag
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            csv_path = args[idx + 1]
        console.print("[cyan]Importing plan data from CSV...[/cyan]")
        try:
            plan = import_plan_from_csv(csv_path)
            save_plan(plan)
            console.print("[green]OK[/green] Plan updated from CSV.")
            console.print("[cyan]Regenerating Gantt chart...[/cyan]")
            generate_gantt()
            console.print("[green]OK[/green] Gantt chart updated!")
        except Exception as e:
            console.print(f"[red]Error importing CSV: {e}[/red]")
        return

    if not plan_path.exists():
        console.print("[yellow]No plan found. Creating one...[/yellow]")
        ensure_project_files()

    if edit_mode:
        # Get editor from environment or use default
        editor = os.environ.get("EDITOR", "notepad" if sys.platform == "win32" else "nano")

        # Open in editor
        console.print(f"[cyan]Opening plan in {editor}...[/cyan]")
        subprocess.run([editor, str(plan_path)])

        # Regenerate Gantt after editing
        console.print("[cyan]Regenerating Gantt chart...[/cyan]")
        try:
            generate_gantt()
            console.print("[green]OK[/green] Gantt chart updated!")
        except Exception as e:
            console.print(f"[red]Error generating Gantt: {e}[/red]")

    else:
        # Show plan
        plan = load_plan()
        plan_json = json.dumps(plan.to_json_list(), indent=2)
        console.print(Markdown(f"```json\n{plan_json}\n```"))


def handle_gantt_command(args: list):
    """Handle /gantt command."""
    open_file = "--open" in args

    try:
        # First, display text-based Gantt chart in CLI
        render_text_gantt(console)

        # Then generate PNG file
        console.print("[cyan]Generating PNG Gantt chart...[/cyan]")
        generate_gantt()
        gantt_path = get_project_root() / "gantt_latest.png"

        console.print(f"[green]OK[/green] Gantt chart saved to {gantt_path}\n")

        if open_file:
            # Open in default image viewer
            if sys.platform == "win32":
                os.startfile(gantt_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(gantt_path)])
            else:
                subprocess.run(["xdg-open", str(gantt_path)])

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def handle_followup_command(args: list):
    """Handle /followup command."""
    if not args:
        console.print("[red]Error: Task description required[/red]")
        console.print("Usage: /followup \"task description\" --due YYYY-MM-DD --assign name")
        return

    # Parse arguments
    task = None
    due = None
    assign = None

    i = 0
    while i < len(args):
        if args[i] == "--due" and i + 1 < len(args):
            due = args[i + 1]
            i += 2
        elif args[i] == "--assign" and i + 1 < len(args):
            assign = args[i + 1]
            i += 2
        elif task is None:
            task = args[i].strip('"\'')
            i += 1
        else:
            i += 1

    if not task:
        console.print("[red]Error: Task description required[/red]")
        return

    # Build prompt for agent
    prompt = f"Add a task: {task}"

    if due:
        prompt += f" with due date {due}"

    if assign:
        prompt += f" assigned to {assign}"

    prompt += ". Also log this decision."

    console.print("[cyan]Adding follow-up task...[/cyan]\n")

    with console.status("[cyan]Processing...[/cyan]"):
        response = run_agent(prompt)

    console.print("[green]OK[/green] " + response)


def render_critical_path():
    """Display the critical path table."""
    plan = load_plan()

    if not plan.tasks:
        console.print("[yellow]No tasks found. Add tasks before running critical path analysis.[/yellow]")
        return

    result = compute_critical_path(plan)
    if not result.tasks:
        console.print("[red]Unable to compute critical path. Check for dependency cycles.[/red]")
        return

    table = Table(title="Critical Path Analysis")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Task", style="white")
    table.add_column("Earliest Window", style="green")
    table.add_column("Latest Window", style="magenta")
    table.add_column("Slack (d)", style="yellow", justify="right")

    for task in result.tasks:
        style = "red" if task.is_critical else "white"
        table.add_row(
            str(task.task_id),
            f"[{style}]{task.title}[/{style}]",
            f"{task.earliest_start} → {task.earliest_finish}",
            f"{task.latest_start} → {task.latest_finish}",
            str(task.slack)
        )

    console.print(table)
    console.print(f"\nProject duration estimate: {result.total_duration} days starting {result.project_start}")

    if result.warnings:
        console.print(Panel("\n".join(result.warnings), title="Warnings", border_style="yellow"))


def render_risk_simulation(
    iterations: int = 2000,
    cost_rate: Optional[float] = None,
    deadline: Optional[date] = None,
    milestone_deadline: Optional[date] = None,
    milestone_label: Optional[str] = None,
    shock_days: int = 0
):
    """Run Monte Carlo schedule risk simulation and display results."""
    plan = load_plan()

    if not plan.tasks:
        console.print("[yellow]No tasks found. Add tasks before running risk simulation.[/yellow]")
        return

    with console.status("[cyan]Running Monte Carlo simulation...[/cyan]"):
        result = run_monte_carlo_simulation(
            plan,
            iterations=iterations,
            cost_rate=cost_rate,
            deadline=deadline,
            milestone_deadline=milestone_deadline,
            shock_days=shock_days,
            milestone_label=milestone_label
        )

    summary = summarize_monte_carlo(result)
    percentiles = result.finish_percentiles
    duration = result.duration_percentiles
    effort = result.effort_percentiles or {}
    spread = duration["p90"] - duration["p10"]

    panel_lines = [
        f"P10 completion: {percentiles['p10']} ({duration['p10']:.1f}d)",
        f"P50 completion: {percentiles['p50']} ({duration['p50']:.1f}d)",
        f"P90 completion: {percentiles['p90']} ({duration['p90']:.1f}d)",
        f"Iterations: {iterations}",
    ]
    if deadline and result.probability_on_time is not None:
        panel_lines.append(f"On-time (<= {deadline}): {result.probability_on_time * 100:.1f}%")
    if milestone_deadline and result.milestone_probability is not None:
        label = milestone_label or f"Tasks due by {milestone_deadline}"
        panel_lines.append(f"{label} on-time: {result.milestone_probability * 100:.1f}%")
    if shock_days:
        panel_lines.append(f"Dependency shock window: up to {shock_days}d")

    console.print(Panel("\n".join(panel_lines), title="Schedule Risk", border_style="cyan"))

    timeline_table = Table(title="Completion Percentiles", show_header=True, header_style="bold cyan")
    timeline_table.add_column("Percentile", style="white")
    timeline_table.add_column("Calendar Date", style="yellow")
    timeline_table.add_column("Duration (days)", style="magenta", justify="right")
    for label in ("p10", "p50", "p90"):
        timeline_table.add_row(
            label.upper(),
            percentiles[label].isoformat(),
            f"{duration[label]:.1f}",
        )
    console.print(timeline_table)

    if effort:
        effort_table = Table(title="Effort Distribution (person-days)", show_header=True, header_style="bold green")
        effort_table.add_column("Percentile", style="white")
        effort_table.add_column("Effort", style="green", justify="right")
        for label in ("p10", "p50", "p90"):
            if label in effort:
                effort_table.add_row(label.upper(), f"{effort[label]:.1f}")
        console.print(effort_table)

    probability_rows = []
    if deadline and result.probability_on_time is not None:
        probability_rows.append((f"Deadline {deadline}", result.probability_on_time))
    if milestone_deadline and result.milestone_probability is not None:
        label = milestone_label or "Milestone"
        probability_rows.append((f"{label} ({milestone_deadline})", result.milestone_probability))
    if probability_rows:
        probability_table = Table(title="Deadline Outlook", show_header=True, header_style="bold magenta")
        probability_table.add_column("Target", style="white")
        probability_table.add_column("Confidence", style="magenta", justify="right")
        for label, value in probability_rows:
            probability_table.add_row(label, f"{value * 100:.1f}%")
        console.print(probability_table)

    if result.risk_drivers:
        table = Table(title="Top Risk Drivers")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Task", style="white")
        table.add_column("Correlation", style="magenta", justify="right")
        table.add_column("Avg Duration (d)", style="yellow", justify="right")

        for driver in result.risk_drivers[:5]:
            table.add_row(
                str(driver.task_id),
                driver.title,
                f"{driver.correlation:.2f}",
                f"{driver.avg_duration:.1f}",
            )

        console.print(table)

    if result.cost_percentiles:
        cost_table = Table(title="Cost Forecast", show_header=True, header_style="bold green")
        cost_table.add_column("Metric", style="white")
        cost_table.add_column("Amount", style="yellow", justify="right")
        cost_table.add_row("P10 Cost", f"${result.cost_percentiles['p10']:,.0f}")
        cost_table.add_row("P50 Cost", f"${result.cost_percentiles['p50']:,.0f}")
        cost_table.add_row("P90 Cost", f"${result.cost_percentiles['p90']:,.0f}")
        console.print(cost_table)

    # Narrative insights panel
    insights: list[str] = []
    finish_stats = summary.get("finish_distribution_stats") or {}
    if finish_stats:
        mean_days = finish_stats.get("mean_days", duration["p50"])
        mean_date = result.project_start + timedelta(days=int(round(mean_days)))
        insights.append(
            f"Average completion is {mean_days:.1f} days from start (around {mean_date})."
        )
        insights.append(
            f"90/10 schedule window spans ~{spread:.1f} days, so keep {spread:.1f} days of buffer."
        )
    if result.risk_drivers:
        primary = result.risk_drivers[0]
        insights.append(
            f"#{primary.task_id} {primary.title} drives the most variance (corr {primary.correlation:.2f})."
        )
    if shock_days:
        insights.append("Dependency shock modeling is enabled; watch tightly coupled chains.")
    if cost_rate and result.cost_percentiles:
        insights.append(
            f"Median burn is ${result.cost_percentiles['p50']:,.0f} with tail up to ${result.cost_percentiles['p90']:,.0f}."
        )
    if not insights:
        insights.append("No additional insights to highlight.")
    console.print(Panel("\n".join(insights), title="Insights", border_style="green"))

    # Persist artifacts
    root = get_project_root()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    artifact_dir = root / "artifacts" / "analysis" / f"monte_carlo_{timestamp}"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "iterations": iterations,
        "cost_rate": cost_rate,
        "deadline": deadline.isoformat() if deadline else None,
        "milestone_deadline": milestone_deadline.isoformat() if milestone_deadline else None,
        "milestone_label": milestone_label,
        "shock_days": shock_days,
    }
    summary_payload = {"parameters": metadata, "results": summary}
    summary_path = artifact_dir / "summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    artifact_paths = [summary_path]

    finish_hist_path = artifact_dir / "finish_distribution.png"
    if save_finish_distribution_plot(result, finish_hist_path):
        artifact_paths.append(finish_hist_path)

    driver_path = artifact_dir / "risk_drivers.png"
    if save_risk_driver_plot(result, driver_path):
        artifact_paths.append(driver_path)

    if cost_rate:
        cost_hist_path = artifact_dir / "cost_distribution.png"
        if save_cost_distribution_plot(result, cost_hist_path):
            artifact_paths.append(cost_hist_path)

    rel_paths = [path.relative_to(root).as_posix() for path in artifact_paths]
    artifact_panel = "\n".join(f"- {path}" for path in rel_paths)
    console.print(Panel(artifact_panel, title="Artifacts saved", border_style="blue"))


def render_health_dashboard(days: int = 45, export: bool = True):
    """Display burndown/burnup, throughput, and slip metrics."""
    history = load_status_history()
    if not history:
        console.print("[yellow]No status history recorded yet. Run /status regularly to build trends.[/yellow]")
        return

    scoped_history = filter_history_by_days(history, days) if days > 0 else history
    if scoped_history:
        history = scoped_history
    elif days > 0:
        console.print(
            f"[yellow]No snapshots within the last {days} days; showing entire history ({len(history)} records).[/yellow]"
        )

    latest = history[-1]
    timeline = derive_task_timelines(history)
    burnup = compute_burnup_series(history)
    throughput = compute_throughput_series(history, timeline=timeline)
    cycle_stats = compute_cycle_time_stats(history, timeline=timeline)
    slip_stats = compute_slip_stats(history, timeline=timeline)

    start_day = history[0].captured_at.date()
    end_day = history[-1].captured_at.date()
    window_panel = Panel(
        f"Snapshots: {len(history)}\nWindow: {start_day} -> {end_day}",
        title="History Window",
        border_style="cyan",
    )
    console.print(window_panel)

    status_table = Table(
        title=f"Latest Snapshot ({latest.captured_at.strftime('%Y-%m-%d %H:%M')} UTC)",
        show_header=True,
        header_style="bold white",
    )
    status_table.add_column("Status")
    status_table.add_column("Count", justify="right")
    for status in ("todo", "in_progress", "blocked", "done"):
        status_table.add_row(status.replace("_", " ").title(), str(latest.status_counts.get(status, 0)))
    status_table.add_row("Total", str(latest.total_tasks))
    console.print(status_table)

    if burnup:
        burn_table = Table(title="Burnup/Burndown", header_style="bold cyan")
        burn_table.add_column("Date", style="white")
        burn_table.add_column("Done/Total", justify="right")
        burn_table.add_column("Remaining", justify="right")
        burn_table.add_column("Blocked", justify="right")
        for point in burnup[-min(len(burnup), 6):]:
            burn_table.add_row(
                point["date"].isoformat(),
                f"{point['done']}/{point['total']}",
                str(point["remaining"]),
                str(point["blocked"]),
            )
        console.print(burn_table)
    else:
        console.print("[yellow]No burnup data yet. Capture a few /status snapshots first.[/yellow]")

    if throughput:
        throughput_table = Table(title="Throughput (completed/day)", header_style="bold green")
        throughput_table.add_column("Date")
        throughput_table.add_column("Completed", justify="right")
        for point in throughput[-min(len(throughput), 7):]:
            throughput_table.add_row(point["date"].isoformat(), str(point["completed"]))
        console.print(throughput_table)
    else:
        console.print("[yellow]No completed tasks recorded in this window.[/yellow]")

    cycle_lines: List[str] = []
    percentiles = cycle_stats.get("percentiles") or {}
    if percentiles:
        cycle_lines.append(f"P50: {percentiles['p50']:.1f}d")
        if "p80" in percentiles:
            cycle_lines.append(f"P80: {percentiles['p80']:.1f}d")
        if "p90" in percentiles:
            cycle_lines.append(f"P90: {percentiles['p90']:.1f}d")
    if cycle_stats.get("mean") is not None:
        cycle_lines.append(f"Mean: {cycle_stats['mean']:.1f}d")
    cycle_lines.append(f"Samples: {cycle_stats.get('count', 0)}")
    console.print(Panel("\n".join(cycle_lines), title="Cycle Time", border_style="magenta"))

    cycle_samples = cycle_stats.get("samples", [])[:3]
    if cycle_samples:
        slow_table = Table(title="Longest Cycle Tasks", header_style="bold magenta")
        slow_table.add_column("Task")
        slow_table.add_column("Cycle (d)", justify="right")
        slow_table.add_column("Completed")
        for sample in cycle_samples:
            slow_table.add_row(
                f"#{sample['task_id']} {sample['title']}",
                f"{sample['cycle_days']:.1f}",
                sample["completed_at"],
            )
        console.print(slow_table)

    slip_lines: List[str] = [
        f"On time / early: {slip_stats.get('on_time', 0)}",
        f"Late: {slip_stats.get('late', 0)}",
    ]
    slip_percentiles = slip_stats.get("percentiles") or {}
    if slip_percentiles:
        slip_lines.append(
            f"Median slip: {slip_percentiles.get('p50', 0):+.1f}d "
            f"(P90: {slip_percentiles.get('p90', 0):+.1f}d)"
        )
    console.print(Panel("\n".join(slip_lines), title="Planned vs Actual", border_style="yellow"))

    late_records = [rec for rec in slip_stats.get("records", []) if rec["slip_days"] > 0][:3]
    if late_records:
        slip_table = Table(title="Most Late Tasks", header_style="bold yellow")
        slip_table.add_column("Task")
        slip_table.add_column("Due -> Done")
        slip_table.add_column("Slip (d)", justify="right")
        for record in late_records:
            slip_table.add_row(
                f"#{record['task_id']} {record['title']}",
                f"{record['due_date']} -> {record['completed_at']}",
                f"+{record['slip_days']}",
            )
        console.print(slip_table)

    if export:
        root = get_project_root()
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifact_dir = root / "artifacts" / "analysis" / f"timeseries_{timestamp}"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "generated_at": datetime.utcnow().isoformat(),
            "window_days": days,
            "history_points": len(history),
            "latest_snapshot": latest.captured_at.isoformat(),
            "burnup": [
                {
                    "date": point["date"].isoformat(),
                    "done": point["done"],
                    "total": point["total"],
                    "remaining": point["remaining"],
                    "blocked": point["blocked"],
                }
                for point in burnup
            ],
            "throughput": [
                {"date": point["date"].isoformat(), "completed": point["completed"]}
                for point in throughput
            ],
            "cycle_time": cycle_stats,
            "slip": slip_stats,
        }
        summary_path = artifact_dir / "health_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        artifact_paths = [summary_path]
        if save_burnup_chart(burnup, artifact_dir / "burnup.png"):
            artifact_paths.append(artifact_dir / "burnup.png")
        if save_throughput_chart(throughput, artifact_dir / "throughput.png"):
            artifact_paths.append(artifact_dir / "throughput.png")
        if save_slip_histogram(slip_stats.get("records", []), artifact_dir / "planned_vs_actual.png"):
            artifact_paths.append(artifact_dir / "planned_vs_actual.png")

        rel_paths = [path.relative_to(root).as_posix() for path in artifact_paths]
        console.print(
            Panel("\n".join(f"- {path}" for path in rel_paths), title="Health artifacts saved", border_style="blue")
        )


def render_workload_dashboard(limit: int = 12, export: bool = True):
    """Show priority ordering and workload/capacity insights."""
    plan = load_plan()
    if not plan.tasks:
        console.print("[yellow]No tasks available. Add tasks before running /workload.[/yellow]")
        return

    def priority_value(task: Task) -> int:
        if not task.priority:
            return 5
        normalized = task.priority.lower()
        mapping = {
            "p0": 0,
            "critical": 0,
            "p1": 1,
            "high": 1,
            "medium": 2,
            "p2": 2,
            "low": 3,
            "p3": 3,
        }
        return mapping.get(normalized, 4)

    def is_high_priority(task: Task) -> bool:
        return priority_value(task) <= 1

    def due_sort_value(task: Task) -> date:
        return task.due_date or date.max

    prioritized = sorted(
        plan.tasks,
        key=lambda task: (priority_value(task), due_sort_value(task), task.id),
    )

    table = Table(title="Priority Backlog", header_style="bold cyan")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Priority", style="red")
    table.add_column("Status", style="magenta")
    table.add_column("Task", style="white")
    table.add_column("Assignee", style="green")
    table.add_column("Due", style="yellow")
    table.add_column("Effort", style="cyan", justify="right")
    table.add_column("Risk", style="magenta")

    for task in prioritized[:limit]:
        table.add_row(
            f"#{task.id}",
            (task.priority or "-").title() if task.priority else "-",
            task.status,
            task.title,
            task.assignee or "Unassigned",
            task.due_date.isoformat() if task.due_date else "-",
            f"{task.effort_points:.1f}" if task.effort_points is not None else "-",
            task.risk_level or "-",
        )
    console.print(table)
    if len(prioritized) > limit:
        console.print(f"[dim]Showing top {limit} of {len(prioritized)} tasks by priority.[/dim]")

    priority_gaps = [
        task for task in prioritized
        if is_high_priority(task) and not task.assignee
    ]
    if priority_gaps:
        gap_lines = [
            f"- #{task.id} {task.title} (priority {task.priority or 'n/a'}, due {task.due_date or 'n/a'})"
            for task in priority_gaps[:5]
        ]
        if len(priority_gaps) > 5:
            gap_lines.append(f"... {len(priority_gaps) - 5} more")
        console.print(Panel("\n".join(gap_lines), title="High priority without owner", border_style="red"))

    workload: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tasks": 0, "effort": 0.0, "high": 0})
    for task in plan.tasks:
        owner = task.assignee or "Unassigned"
        workload[owner]["tasks"] += 1
        if task.effort_points is not None:
            workload[owner]["effort"] += float(task.effort_points)
        if is_high_priority(task):
            workload[owner]["high"] += 1

    workload_rows = sorted(
        workload.items(),
        key=lambda item: (-item[1]["effort"], -item[1]["tasks"], item[0].lower()),
    )
    load_table = Table(title="Capacity by Assignee", header_style="bold green")
    load_table.add_column("Owner", style="green")
    load_table.add_column("Tasks", justify="right")
    load_table.add_column("Effort (pts)", justify="right")
    load_table.add_column("High Priority", justify="right")
    for owner, stats in workload_rows:
        effort_val = f"{stats['effort']:.1f}" if stats["effort"] else "-"
        load_table.add_row(owner, str(stats["tasks"]), effort_val, str(int(stats["high"])))
    console.print(load_table)

    risk_counts = Counter((task.risk_level or "unspecified").title() for task in plan.tasks)
    if risk_counts:
        risk_table = Table(title="Risk Exposure", header_style="bold yellow")
        risk_table.add_column("Risk Level")
        risk_table.add_column("Tasks", justify="right")
        for level, count in risk_counts.most_common():
            risk_table.add_row(level, str(count))
        console.print(risk_table)

    okr_counts = Counter(task.okr_alignment for task in plan.tasks if task.okr_alignment)
    if okr_counts:
        okr_table = Table(title="OKR Alignment", header_style="bold blue")
        okr_table.add_column("OKR / Outcome")
        okr_table.add_column("Tasks", justify="right")
        for okr, count in okr_counts.most_common():
            okr_table.add_row(okr, str(count))
        console.print(okr_table)

    if export:
        root = get_project_root()
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifact_dir = root / "artifacts" / "analysis" / f"workload_{timestamp}"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "generated_at": datetime.utcnow().isoformat(),
            "priority_backlog": [
                {
                    "id": task.id,
                    "title": task.title,
                    "priority": task.priority,
                    "assignee": task.assignee,
                    "due_date": task.due_date.isoformat() if task.due_date else None,
                    "effort_points": task.effort_points,
                    "risk_level": task.risk_level,
                }
                for task in prioritized
            ],
            "workload": workload,
            "risk_counts": dict(risk_counts),
            "okr_counts": dict(okr_counts),
            "priority_gaps": [task.id for task in priority_gaps],
        }
        summary_path = artifact_dir / "workload_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        rel_paths = [summary_path.relative_to(root).as_posix()]
        console.print(Panel("\n".join(f"- {path}" for path in rel_paths), title="Workload artifacts saved", border_style="blue"))


def render_focus_board(horizon_days: int = 14, limit: int = 10):
    """Generate and display the near-term focus list."""
    plan = load_plan()
    if not plan.tasks:
        console.print("[yellow]Add tasks first to build a focus list.[/yellow]")
        return

    board = build_focus_board(plan, horizon_days=horizon_days, limit=limit)
    save_focus_board(board)

    if not board.items:
        console.print(f"[green]No tasks due within {horizon_days} days or blocking progress.[/green]")
        return

    table = Table(title=f"Focus List - Next {horizon_days} Days")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Task", style="white")
    table.add_column("Due", style="yellow")
    table.add_column("Owner", style="green")
    table.add_column("Status", style="magenta")
    table.add_column("Reason", style="red")

    for item in board.items:
        days = f"{item.days_remaining}d" if item.days_remaining is not None else "-"
        reason = item.reason.replace("_", " ").title()
        table.add_row(
            str(item.task_id or "?"),
            item.title,
            item.due_date.isoformat() if item.due_date else "-",
            item.assignee or "Unassigned",
            item.status or "-",
            f"{reason} ({days})"
        )

    console.print(table)
    console.print("[dim]Saved to 05_focus.json[/dim]")


def append_decision_log(message: str, entry_type: str = "note", tags: Optional[list] = None, task_id: Optional[str] = None):
    """Append a structured entry to 04_decisions.log."""
    root = get_project_root()
    log_path = root / "04_decisions.log"
    tags = tags or []

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## {timestamp} [{entry_type.upper()}]"]
    if task_id:
        lines.append(f"Task: #{task_id}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    lines.append(message)
    lines.append("")

    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    console.print(f"[green]Logged entry in {log_path.name}[/green]")


def render_artifacts(limit: int = 15):
    """List recent artifacts stored under artifacts/."""
    root = get_project_root()
    items = summarize_artifacts(root, max_items=limit)

    if not items:
        console.print("[yellow]No artifacts found. Save Gantt charts or documentation to artifacts/.[/yellow]")
        return

    table = Table(title="Artifacts Archive")
    table.add_column("Path", style="white")
    table.add_column("Size (KB)", style="yellow", justify="right")
    table.add_column("Last Modified", style="green")

    for item in items:
        table.add_row(
            item.path,
            f"{item.size_kb:.1f}",
            item.modified.strftime("%Y-%m-%d %H:%M")
        )

    console.print(table)


def generate_documentation(
    output_path: Optional[str] = None,
    iterations: int = 2000,
    focus_days: int = 14
):
    """Generate a comprehensive Word document with current project data."""
    plan = load_plan()
    focus_board = build_focus_board(plan, horizon_days=focus_days, limit=20)
    save_focus_board(focus_board)
    critical_path = compute_critical_path(plan)
    risk_result = run_monte_carlo_simulation(plan, iterations=iterations)
    root = get_project_root()
    artifacts = summarize_artifacts(root, max_items=50)

    if output_path:
        doc_path = Path(output_path)
        if not doc_path.is_absolute():
            doc_path = root / doc_path
    else:
        doc_path = root / "artifacts" / "documentation" / f"{root.name}_project_brief.docx"

    doc_path.parent.mkdir(parents=True, exist_ok=True)

    console.print("[cyan]Refreshing Gantt chart for the report...[/cyan]")
    generate_gantt()
    gantt_path = root / "gantt_latest.png"

    with console.status("[cyan]Building documentation...[/cyan]"):
        output_file = generate_documentation_report(
            plan=plan,
            focus_board=focus_board,
            critical_path=critical_path,
            monte_carlo=risk_result,
            artifacts=artifacts,
            output_path=doc_path,
            project_root=root,
            gantt_path=gantt_path,
        )

    console.print(f"[green]Documentation saved to {output_file}[/green]")


def handle_new_command(args: list):
    """Handle /new command."""
    if not args:
        console.print("[red]Error: Project name required[/red]")
        console.print("Usage: /new <project-name>")
        return

    base_name = args[0].strip()
    if not base_name:
        console.print("[red]Error: Project name cannot be empty[/red]")
        return

    def _generate_project_id(counter: int = 0) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return f"{timestamp}-{counter}" if counter else timestamp

    counter = 0
    project_id = _generate_project_id(counter)
    project_dir_name = f"{base_name}-{project_id}"
    project_path = Path(project_dir_name)

    while project_path.exists():
        counter += 1
        project_id = _generate_project_id(counter)
        project_dir_name = f"{base_name}-{project_id}"
        project_path = Path(project_dir_name)

    # Create project directory
    project_path.mkdir(parents=True)

    # Change to project directory
    os.chdir(project_path)

    # Create all standard files
    ensure_project_files()

    metadata = {
        "project_name": base_name,
        "project_id": project_id,
        "folder": project_dir_name,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    manifest_path = project_path / ".pm" / "project.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    console.print(f"[green]OK[/green] Project '{base_name}' created successfully!")
    console.print(f"[cyan]Assigned ID:[/cyan] {project_id}")
    console.print(f"[cyan]Directory:[/cyan] {project_dir_name}")
    console.print(f"[cyan]Switched to project directory[/cyan]")


def handle_archive_command(args: list):
    """Handle /archive command."""
    current_dir = get_project_root()
    project_name = current_dir.name

    # Create archive directory in parent
    archive_dir = current_dir.parent / "_archive"
    archive_dir.mkdir(exist_ok=True)

    # Create archive name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d")
    archive_name = f"{project_name}_done_{timestamp}"
    archive_path = archive_dir / archive_name

    # Move project
    console.print(f"[cyan]Archiving project to {archive_path}...[/cyan]")

    try:
        current_dir.rename(archive_path)
        console.print(f"[green]OK[/green] Project archived successfully!")
        console.print("[yellow]Note: You've been moved out of the project directory[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def handle_critical_path_command(args: list):
    """Adapter for slash/CLI invocations."""
    render_critical_path()


def handle_risk_command(args: list):
    """Adapter for running Monte Carlo analysis."""
    iterations = 2000
    i = 0
    while i < len(args):
        if args[i] in ("--iterations", "-n") and i + 1 < len(args):
            try:
                iterations = int(args[i + 1])
            except ValueError:
                console.print("[red]--iterations expects an integer[/red]")
                return
            i += 2
        else:
            i += 1
    render_risk_simulation(iterations)


def handle_forecast_command(args: list):
    """Advanced Monte Carlo forecast with optional cost/deadline modifiers."""
    iterations = 2000
    cost_rate = None
    deadline = None
    milestone = None
    milestone_label = None
    shock_days = 0

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--iterations", "-n") and i + 1 < len(args):
            try:
                iterations = int(args[i + 1])
            except ValueError:
                console.print("[red]--iterations expects an integer[/red]")
                return
            i += 2
        elif token == "--cost-rate" and i + 1 < len(args):
            try:
                cost_rate = float(args[i + 1])
            except ValueError:
                console.print("[red]--cost-rate expects a number[/red]")
                return
            i += 2
        elif token == "--deadline" and i + 1 < len(args):
            try:
                deadline = parse_date_option(args[i + 1])
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                return
            i += 2
        elif token == "--milestone" and i + 1 < len(args):
            try:
                milestone = parse_date_option(args[i + 1])
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                return
            i += 2
        elif token == "--milestone-label" and i + 1 < len(args):
            milestone_label = args[i + 1]
            i += 2
        elif token == "--shock" and i + 1 < len(args):
            try:
                shock_days = int(args[i + 1])
            except ValueError:
                console.print("[red]--shock expects an integer (days)[/red]")
                return
            if shock_days < 0:
                console.print("[red]--shock must be zero or positive[/red]")
                return
            i += 2
        else:
            i += 1

    render_risk_simulation(
        iterations=iterations,
        cost_rate=cost_rate,
        deadline=deadline,
        milestone_deadline=milestone,
        milestone_label=milestone_label,
        shock_days=shock_days
    )


def handle_health_command(args: list):
    """Adapter for the timeline health dashboard."""
    days = 45
    export = True
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--days" and i + 1 < len(args):
            try:
                days = int(args[i + 1])
            except ValueError:
                console.print("[red]--days expects an integer[/red]")
                return
            i += 2
        elif token == "--no-export":
            export = False
            i += 1
        else:
            i += 1

    render_health_dashboard(days=days, export=export)


def handle_workload_command(args: list):
    """Adapter for the workload dashboard."""
    limit = 12
    export = True
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                console.print("[red]--limit expects an integer[/red]")
                return
            i += 2
        elif token == "--no-export":
            export = False
            i += 1
        else:
            i += 1

    render_workload_dashboard(limit=limit, export=export)


def handle_focus_command(args: list):
    """Adapter for building/showing the focus list."""
    horizon = 14
    limit = 10
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            try:
                horizon = int(args[i + 1])
            except ValueError:
                console.print("[red]--days expects an integer[/red]")
                return
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                console.print("[red]--limit expects an integer[/red]")
                return
            i += 2
        else:
            i += 1

    render_focus_board(horizon_days=horizon, limit=limit)


def handle_log_command(args: list):
    """Adapter for writing structured log entries."""
    if not args:
        console.print("[red]Usage: /log \"message\" [--type decision] [--task 3] [--tags a,b][/red]")
        return

    entry_type = "note"
    tags: list = []
    task_id = None
    message_parts = []

    i = 0
    while i < len(args):
        token = args[i]
        if token == "--type" and i + 1 < len(args):
            entry_type = args[i + 1]
            i += 2
        elif token == "--task" and i + 1 < len(args):
            task_id = args[i + 1]
            i += 2
        elif token == "--tags" and i + 1 < len(args):
            tags = [tag.strip() for tag in args[i + 1].split(",") if tag.strip()]
            i += 2
        else:
            message_parts.append(token)
            i += 1

    message = " ".join(message_parts).strip()
    if not message:
        console.print("[red]Log message cannot be empty.[/red]")
        return

    append_decision_log(message, entry_type, tags, task_id)


def handle_documentation_command(args: list):
    """Adapter for generating documentation."""
    output_path = None
    iterations = 2000
    focus_days = 14

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--output", "-o") and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        elif token in ("--iterations", "-n") and i + 1 < len(args):
            try:
                iterations = int(args[i + 1])
            except ValueError:
                console.print("[red]--iterations expects an integer[/red]")
                return
            i += 2
        elif token == "--focus-days" and i + 1 < len(args):
            try:
                focus_days = int(args[i + 1])
            except ValueError:
                console.print("[red]--focus-days expects an integer[/red]")
                return
            i += 2
        else:
            i += 1

    generate_documentation(output_path=output_path, iterations=iterations, focus_days=focus_days)


def handle_artifacts_command(args: list):
    """Adapter for listing artifacts."""
    limit = 15
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                console.print("[red]--limit expects an integer[/red]")
                return
            i += 2
        else:
            i += 1

    render_artifacts(limit=limit)


def parse_slash_command(user_input: str):
    """Parse slash command and execute it."""
    # Remove leading slash and split into parts
    try:
        parts = shlex.split(user_input[1:])
    except ValueError as exc:
        console.print(f"[red]Error parsing command: {exc}[/red]")
        return False

    if not parts:
        return False

    command = parts[0].lower()
    args = parts[1:]

    if command in ["exit", "quit", "q"]:
        return "exit"
    elif command == "help" or command == "h":
        show_help()
    elif command == "status":
        handle_status_command(args)
    elif command == "plan":
        handle_plan_command(args)
    elif command == "gantt":
        handle_gantt_command(args)
    elif command == "followup":
        handle_followup_command(args)
    elif command in ("criticalpath", "cp"):
        handle_critical_path_command(args)
    elif command in ("risk", "montecarlo"):
        handle_risk_command(args)
    elif command == "forecast":
        handle_forecast_command(args)
    elif command == "health":
        handle_health_command(args)
    elif command == "workload":
        handle_workload_command(args)
    elif command == "focus":
        handle_focus_command(args)
    elif command == "log":
        handle_log_command(args)
    elif command in ("documentation", "doc"):
        handle_documentation_command(args)
    elif command in ("artifacts", "artifact"):
        handle_artifacts_command(args)
    elif command == "new":
        handle_new_command(args)
    elif command == "archive":
        handle_archive_command(args)
    else:
        console.print(f"[red]Unknown command: /{command}[/red]")
        console.print("Type [cyan]/help[/cyan] for available commands")

    return True


def show_startup_animation():
    """Display a cool startup animation with logo and mascot."""

    # Check if terminal supports Unicode by checking encoding
    supports_unicode = True
    if sys.platform == "win32":
        # Try to enable UTF-8 mode on Windows
        try:
            import codecs
            sys.stdout.reconfigure(encoding='utf-8')
        except:
            supports_unicode = False

    if supports_unicode:
        # Unicode version (prettier)
        LOGO = """
    ██████╗ ██████╗ ███╗   ██╗████████╗██████╗  ██████╗ ██╗
   ██╔════╝██╔═══██╗████╗  ██║╚══██╔══╝██╔══██╗██╔═══██╗██║
   ██║     ██║   ██║██╔██╗ ██║   ██║   ██████╔╝██║   ██║██║
   ██║     ██║   ██║██║╚██╗██║   ██║   ██╔══██╗██║   ██║██║
   ╚██████╗╚██████╔╝██║ ╚████║   ██║   ██║  ██║╚██████╔╝███████╗
    ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝

   ████████╗ ██████╗ ██╗    ██╗███████╗██████╗
   ╚══██╔══╝██╔═══██╗██║    ██║██╔════╝██╔══██╗
      ██║   ██║   ██║██║ █╗ ██║█████╗  ██████╔╝
      ██║   ██║   ██║██║███╗██║██╔══╝  ██╔══██╗
      ██║   ╚██████╔╝╚███╔███╔╝███████╗██║  ██║
      ╚═╝    ╚═════╝  ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝
"""
        MASCOT = """

"""
    else:
        # ASCII fallback (Windows compatible)
        LOGO = r"""
     ____ ___  _   _ _____ ____   ___  _
    / ___/ _ \| \ | |_   _|  _ \ / _ \| |
   | |  | | | |  \| | | | | |_) | | | | |
   | |__| |_| | |\  | | | |  _ <| |_| | |___
    \____\___/|_| \_| |_| |_| \_\\___/|_____|

    _____ _____        _______ ____
   |_   _/ _ \ \      / / ____|  _ \
     | || | | \ \ /\ / /|  _| | |_) |
     | || |_| |\ V  V / | |___|  _ <
     |_| \___/  \_/\_/  |_____|_| \_\
"""
        MASCOT = """

"""

    TAGLINE = "AI-Powered Project Management Control Tower"

    try:
        with Live(console=console, refresh_per_second=20, transient=True) as live:
            # Frame 1: Dots appear
            text1 = Text()
            text1.append("...", style="dim cyan")
            live.update(Align.center(text1))
            time.sleep(0.15)

            # Frame 2: More dots
            text2 = Text()
            text2.append("......", style="cyan")
            live.update(Align.center(text2))
            time.sleep(0.15)

            # Frame 3: Logo fades in
            text3 = Text()
            text3.append(LOGO, style="dim cyan")
            live.update(Align.center(text3))
            time.sleep(0.2)

            # Frame 4: Logo brightens
            text4 = Text()
            text4.append(LOGO, style="cyan")
            live.update(Align.center(text4))
            time.sleep(0.15)

            # Frame 5: Logo full brightness with mascot
            text5 = Text()
            text5.append(LOGO, style="bold bright_cyan")
            text5.append("\n")
            text5.append(MASCOT, style="magenta")
            live.update(Align.center(text5))
            time.sleep(0.2)

            # Frame 6: Add tagline
            text6 = Text()
            text6.append(LOGO, style="bold bright_cyan")
            text6.append("\n")
            text6.append(MASCOT, style="magenta")
            text6.append("\n")
            text6.append(f"        {TAGLINE}", style="bold cyan")
            live.update(Align.center(text6))
            time.sleep(0.3)

        # After animation completes, print the final logo persistently
        final_display = Text()
        final_display.append(LOGO, style="bold bright_cyan")
        final_display.append("\n")
        final_display.append(MASCOT, style="magenta")
        final_display.append("\n")
        final_display.append(f"        {TAGLINE}", style="bold cyan")
        final_display.append("\n")
        console.print(Align.center(final_display))

    except (UnicodeEncodeError, Exception):
        # Simple fallback
        console.print("\n[bold cyan]CONTROL TOWER[/bold cyan]", justify="center")
        console.print("[dim cyan]AI-Powered Project Management[/dim cyan]", justify="center")
        console.print()


def start_repl(project: Optional[str] = None):
    """Start the main REPL interface."""
    # Show startup animation (can be disabled with PM_NO_ANIMATION=1)
    if not os.environ.get("PM_NO_ANIMATION"):
        show_startup_animation()

    # Change to project directory if specified
    if project:
        if not Path(project).exists():
            console.print(f"[red]Error: Project '{project}' not found[/red]")
            return
        os.chdir(project)

    # Ensure project files exist
    try:
        ensure_project_files()
    except Exception:
        # Not in a project directory, that's okay for /new
        pass

    # Create agent
    agent = create_agent()
    config = {"configurable": {"thread_id": "main"}}

    # prompt_toolkit session for command history + completion
    session = PromptSession(
        completer=SlashCommandCompleter(),
        auto_suggest=AutoSuggestFromHistory(),
        complete_in_thread=True,
    )

    # Show welcome message
    console.print(Panel.fit(
        "[bold cyan]Control Tower[/bold cyan] - AI-Powered Project Management\n\n"
        "Type naturally to chat with the AI, or use slash commands.\n"
        "Type [bold]/help[/bold] for commands or [bold]/exit[/bold] to quit.",
        border_style="cyan"
    ))

    # Check if we're in a project
    try:
        root = get_project_root()
        console.print(f"\n[dim]Current project: {root.name}[/dim]\n")
        try:
            focus_board = load_focus_board()
            if focus_board.items:
                preview_rows = []
                for item in focus_board.items[:3]:
                    due = item.due_date.isoformat() if item.due_date else "-"
                    preview_rows.append(
                        f"#{item.task_id or '?'} {item.title} ({item.status or 'todo'}) → {due}"
                    )
                console.print(Panel(
                    "\n".join(preview_rows),
                    title="Near-Term Focus",
                    border_style="magenta"
                ))
                console.print()
        except Exception:
            pass
    except Exception:
        console.print(f"\n[yellow]Not in a project directory. Use /new <name> to create one.[/yellow]\n")

    # Main REPL loop
    while True:
        try:
            # Get user input (with slash completion)
            user_input = session.prompt(HTML("<ansicyan><b>></b></ansicyan> "))

            if not user_input.strip():
                continue

            # Check if it's a slash command
            if user_input.startswith("/"):
                result = parse_slash_command(user_input)
                if result == "exit":
                    console.print("[yellow]Goodbye![/yellow]")
                    break
                continue

            # Otherwise, send to AI agent
            with console.status("[cyan]Agent is thinking...[/cyan]"):
                response = agent.invoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=config
                )

            # Extract and display response
            final_message = response["messages"][-1]
            console.print(f"\n[bold green]Agent:[/bold green]")
            console.print(Markdown(final_message.content))
            console.print()

        except KeyboardInterrupt:
            console.print("\n[yellow]Goodbye![/yellow]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    project: Optional[str] = typer.Argument(None, help="Project directory to work with")
):
    """
    PM Agent - AI-Powered Project Manager

    Run without arguments to enter interactive mode, or use specific commands.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand, start REPL
        start_repl(project)


# Keep legacy commands for backwards compatibility
@app.command()
def new(name: str):
    """Create a new project with the standard structure."""
    handle_new_command([name])
    console.print(f"\nNext steps:")
    console.print(f"  cd {name}")
    console.print(f"  pm")


@app.command()
def status(week: bool = typer.Option(False, "--week", help="Get weekly summary")):
    """Show the current project status."""
    args = ["--week"] if week else []
    handle_status_command(args)


@app.command()
def plan(
    edit: bool = typer.Option(False, "--edit", help="Edit plan in editor"),
    update_from_csv: bool = typer.Option(
        False,
        "--update-from-csv",
        help="Import plan data from CSV (default: plan.csv)"
    ),
    csv_path: Optional[str] = typer.Option(
        None,
        "--csv-path",
        help="Path to plan CSV (used with --update-from-csv)"
    ),
):
    """Show or edit the project plan."""
    args = []
    if edit:
        args.append("--edit")
    if update_from_csv:
        args.append("--update-from-csv")
        if csv_path:
            args.append(csv_path)
    handle_plan_command(args)


@app.command()
def gantt(open_file: bool = typer.Option(False, "--open", help="Open the Gantt chart")):
    """Generate and optionally open the Gantt chart."""
    args = ["--open"] if open_file else []
    handle_gantt_command(args)


@app.command()
def followup(
    task: str = typer.Argument(..., help="The task to add"),
    due: Optional[str] = typer.Option(None, "--due", help="Due date (YYYY-MM-DD)"),
    assign: Optional[str] = typer.Option(None, "--assign", help="Assignee")
):
    """Quickly add a follow-up task."""
    args = [task]
    if due:
        args.extend(["--due", due])
    if assign:
        args.extend(["--assign", assign])
    handle_followup_command(args)


@app.command()
def archive():
    """Archive the current project."""
    handle_archive_command([])


@app.command()
def criticalpath():
    """Run a critical path analysis."""
    render_critical_path()


@app.command()
def risk(iterations: int = typer.Option(2000, "--iterations", "-n", help="Number of Monte Carlo runs")):
    """Run Monte Carlo schedule risk simulation."""
    render_risk_simulation(iterations)


@app.command()
def forecast(
    iterations: int = typer.Option(2000, "--iterations", "-n", help="Number of Monte Carlo runs"),
    cost_rate: Optional[float] = typer.Option(None, "--cost-rate", help="Cost per day used for cost forecasting"),
    deadline: Optional[str] = typer.Option(None, "--deadline", help="Target deadline (YYYY-MM-DD)"),
    milestone: Optional[str] = typer.Option(None, "--milestone", help="Milestone deadline for subset probability"),
    milestone_label: Optional[str] = typer.Option(None, "--milestone-label", help="Label for milestone probability"),
    shock: int = typer.Option(0, "--shock", help="Maximum random delay (days) applied to dependent tasks")
):
    """Advanced Monte Carlo forecast with deadline, cost, and shock options."""
    if shock < 0:
        typer.echo("Shock must be zero or positive.")
        raise typer.Exit(1)

    deadline_date = None
    milestone_date = None
    try:
        if deadline:
            deadline_date = parse_date_option(deadline)
        if milestone:
            milestone_date = parse_date_option(milestone)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)

    render_risk_simulation(
        iterations=iterations,
        cost_rate=cost_rate,
        deadline=deadline_date,
        milestone_deadline=milestone_date,
        milestone_label=milestone_label,
        shock_days=shock
    )


@app.command()
def health(
    days: int = typer.Option(45, "--days", help="History window to display (days, <=0 for full history)"),
    export: bool = typer.Option(True, "--export/--no-export", help="Persist artifacts under artifacts/analysis")
):
    """Show time-series health dashboard and optional artifacts."""
    render_health_dashboard(days=days, export=export)


@app.command()
def workload(
    limit: int = typer.Option(12, "--limit", help="How many tasks to show in the top priority table"),
    export: bool = typer.Option(True, "--export/--no-export", help="Persist workload summary under artifacts/analysis")
):
    """Show prioritization/workload overview and optional artifacts."""
    render_workload_dashboard(limit=limit, export=export)


@app.command()
def focus(
    days: int = typer.Option(14, "--days", help="Planning horizon in days"),
    limit: int = typer.Option(10, "--limit", help="Max number of items to show")
):
    """Display the near-term focus list."""
    render_focus_board(horizon_days=days, limit=limit)


@app.command()
def log(
    entry: str = typer.Argument(..., help="Message to log"),
    entry_type: str = typer.Option("note", "--type", help="Entry type, e.g., decision, issue"),
    task: Optional[str] = typer.Option(None, "--task", help="Related task ID"),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags")
):
    """Append an entry to 04_decisions.log."""
    tags_list = [tag.strip() for tag in tags.split(",")] if tags else []
    append_decision_log(entry, entry_type, tags_list, task)


@app.command()
def documentation(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
    iterations: int = typer.Option(2000, "--iterations", "-n", help="Monte Carlo iterations"),
    focus_days: int = typer.Option(14, "--focus-days", help="Focus list horizon in days")
):
    """Generate a comprehensive Word document with project status."""
    generate_documentation(output_path=output, iterations=iterations, focus_days=focus_days)


@app.command()
def artifacts(limit: int = typer.Option(15, "--limit", help="Number of artifacts to list")):
    """List the latest files stored under artifacts/."""
    render_artifacts(limit=limit)


if __name__ == "__main__":
    app()

# ---------- FILE: reporting.py ----------
"""Documentation/report generation helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from docx import Document
from docx.shared import Inches

from .analysis import ArtifactInfo, CriticalPathResult, MonteCarloResult
from .schema import FocusBoard, ProjectPlan


def _read_file(path: Path, fallback: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


def generate_documentation_report(
    plan: ProjectPlan,
    focus_board: FocusBoard,
    critical_path: CriticalPathResult,
    monte_carlo: MonteCarloResult,
    artifacts: List[ArtifactInfo],
    output_path: Path,
    project_root: Path,
    gantt_path: Path,
) -> Path:
    """Create a Word report that unifies the project's key artifacts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    doc.add_heading(f"{project_root.name} Project Brief", 0)
    doc.add_paragraph(
        f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        "via PM Agent automated documentation."
    )

    # Charter
    charter_text = _read_file(
        project_root / "00_project_charter.md",
        "Charter not yet created.",
    )
    doc.add_heading("Project Charter", level=1)
    doc.add_paragraph(charter_text)

    # Plan snapshot
    doc.add_heading("Plan Snapshot", level=1)
    plan_table = doc.add_table(rows=1, cols=5)
    plan_table.style = "Medium List 1 Accent 1"
    plan_table.autofit = True
    hdr_cells = plan_table.rows[0].cells
    hdr_cells[0].text = "ID"
    hdr_cells[1].text = "Title"
    hdr_cells[2].text = "Owner"
    hdr_cells[3].text = "Due"
    hdr_cells[4].text = "Status"

    for task in plan.tasks[:15]:
        row_cells = plan_table.add_row().cells
        row_cells[0].text = str(task.id)
        row_cells[1].text = task.title
        row_cells[2].text = task.assignee or "Unassigned"
        row_cells[3].text = task.due_date.isoformat() if task.due_date else "-"
        row_cells[4].text = task.status

    if len(plan.tasks) > 15:
        doc.add_paragraph(f"(Showing first 15 of {len(plan.tasks)} tasks)")

    # Critical path
    doc.add_heading("Critical Path Analysis", level=1)
    if critical_path.tasks:
        cp_table = doc.add_table(rows=1, cols=5)
        cp_table.style = "Medium Shading 1 Accent 2"
        cp_hdr = cp_table.rows[0].cells
        cp_hdr[0].text = "Task"
        cp_hdr[1].text = "Earliest"
        cp_hdr[2].text = "Latest"
        cp_hdr[3].text = "Slack (d)"
        cp_hdr[4].text = "Critical?"
        for task in critical_path.tasks:
            row = cp_table.add_row().cells
            row[0].text = f"#{task.task_id} {task.title}"
            row[1].text = f"{task.earliest_start} \u2192 {task.earliest_finish}"
            row[2].text = f"{task.latest_start} \u2192 {task.latest_finish}"
            row[3].text = str(task.slack)
            row[4].text = "Yes" if task.is_critical else "No"
    else:
        doc.add_paragraph("No critical path available (plan empty).")

    if critical_path.warnings:
        doc.add_paragraph("Warnings:")
        for warning in critical_path.warnings:
            doc.add_paragraph(f"- {warning}")

    # Monte Carlo risk
    doc.add_heading("Schedule Risk Outlook", level=1)
    risk_paragraph = doc.add_paragraph()
    percentiles = monte_carlo.finish_percentiles
    risk_paragraph.add_run(
        f"P50 completion: {percentiles['p50']}, "
        f"P90 completion: {percentiles['p90']}."
    )
    doc.add_paragraph(
        "Monte Carlo percentiles are calculated from thousands of simulations "
        "using the task-level duration estimates."
    )
    if monte_carlo.risk_drivers:
        doc.add_paragraph("Top risk drivers:")
        for driver in monte_carlo.risk_drivers[:5]:
            doc.add_paragraph(
                f"- #{driver.task_id} {driver.title}: "
                f"correlation {driver.correlation:.2f}, "
                f"avg duration {driver.avg_duration:.1f} days."
            )

    # Focus board
    doc.add_heading("Near-Term Focus", level=1)
    if focus_board.items:
        focus_table = doc.add_table(rows=1, cols=5)
        focus_table.style = "Light List"
        focus_hdr = focus_table.rows[0].cells
        focus_hdr[0].text = "Task"
        focus_hdr[1].text = "Due"
        focus_hdr[2].text = "Owner"
        focus_hdr[3].text = "Status"
        focus_hdr[4].text = "Reason"
        for item in focus_board.items:
            row = focus_table.add_row().cells
            row[0].text = f"#{item.task_id or '?'} {item.title}"
            row[1].text = item.due_date.isoformat() if item.due_date else "-"
            row[2].text = item.assignee or "Unassigned"
            row[3].text = item.status or "-"
            label = item.reason.replace("_", " ").title()
            if item.days_remaining is not None:
                label += f" ({item.days_remaining}d)"
            row[4].text = label
    else:
        doc.add_paragraph("No immediate tasks in the focus list.")

    # Logs and risks
    doc.add_heading("Risks & Issues", level=1)
    doc.add_paragraph(
        _read_file(project_root / "03_risks_issues.md", "No risks/issues captured yet.")
    )

    doc.add_heading("Decision Log Highlights", level=1)
    decisions_text = _read_file(project_root / "04_decisions.log", "No decisions logged.")
    doc.add_paragraph(decisions_text)

    # Artifacts inventory
    doc.add_heading("Artifacts Inventory", level=1)
    if artifacts:
        for art in artifacts:
            doc.add_paragraph(
                f"- {art.path} ({art.size_kb:.1f} KB, updated {art.modified:%Y-%m-%d %H:%M})"
            )
    else:
        doc.add_paragraph("No artifacts stored yet.")

    # Gantt image
    doc.add_heading("Gantt Timeline", level=1)
    if gantt_path.exists():
        doc.add_picture(str(gantt_path), width=Inches(6))
    else:
        doc.add_paragraph("No Gantt chart available.")

    doc.save(output_path)
    return output_path

# ---------- FILE: schema.py ----------
"""Data models for the PM Agent."""

from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class Task(BaseModel):
    """A single task in the project plan."""

    id: int
    title: str
    assignee: Optional[str] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    status: str = "todo"  # todo, in_progress, done, blocked
    dependencies: List[int] = Field(default_factory=list)
    optimistic_days: Optional[int] = None
    likely_days: Optional[int] = None
    pessimistic_days: Optional[int] = None
    priority: Optional[str] = None  # e.g., critical/high/medium/low
    effort_points: Optional[float] = None
    cost_estimate: Optional[float] = None
    risk_level: Optional[str] = None
    sprint: Optional[str] = None
    okr_alignment: Optional[str] = None
    deliverable_type: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat() if v else None
        }


class ProjectPlan(BaseModel):
    """The complete project plan."""

    tasks: List[Task] = Field(default_factory=list)

    def get_next_id(self) -> int:
        """Get the next available task ID."""
        if not self.tasks:
            return 1
        return max(task.id for task in self.tasks) + 1

    def get_task(self, task_id: int) -> Optional[Task]:
        """Find a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def to_json_list(self) -> List[dict]:
        """Convert to JSON-serializable list format."""
        return [
            {
                "id": task.id,
                "title": task.title,
                "assignee": task.assignee,
                "start_date": task.start_date.isoformat() if task.start_date else None,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "status": task.status,
                "dependencies": task.dependencies,
                "actual_start_date": task.actual_start_date.isoformat() if task.actual_start_date else None,
                "actual_end_date": task.actual_end_date.isoformat() if task.actual_end_date else None,
                "optimistic_days": task.optimistic_days,
                "likely_days": task.likely_days,
                "pessimistic_days": task.pessimistic_days,
                "priority": task.priority,
                "effort_points": task.effort_points,
                "cost_estimate": task.cost_estimate,
                "risk_level": task.risk_level,
                "sprint": task.sprint,
                "okr_alignment": task.okr_alignment,
                "deliverable_type": task.deliverable_type,
                "tags": task.tags,
            }
            for task in self.tasks
        ]


class FocusItem(BaseModel):
    """A near-term focus item derived from the plan."""

    task_id: Optional[int] = None
    title: str
    due_date: Optional[date] = None
    status: Optional[str] = None
    assignee: Optional[str] = None
    days_remaining: Optional[int] = None
    reason: str = "due_soon"  # due_soon, overdue, blocked

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat() if v else None
        }


class FocusBoard(BaseModel):
    """Represents the near-term focus list."""

    generated_at: Optional[datetime] = None
    horizon_days: int = 14
    max_items: int = 10
    items: List[FocusItem] = Field(default_factory=list)

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat() if v else None,
            datetime: lambda v: v.isoformat() if v else None
        }

# ---------- FILE: tools.py ----------
"""Tools for reading and writing project files."""

import csv
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict
from langchain_core.tools import tool

from .schema import ProjectPlan, Task, FocusBoard
from .analysis import (
    build_focus_board,
    compute_critical_path,
    run_monte_carlo_simulation,
    summarize_artifacts,
)


def get_project_root() -> Path:
    """Get the current project root directory."""
    return Path.cwd()


PLAN_CSV_FIELDS = [
    "id",
    "title",
    "assignee",
    "start_date",
    "due_date",
    "actual_start_date",
    "actual_end_date",
    "status",
    "dependencies",
    "optimistic_days",
    "likely_days",
    "pessimistic_days",
    "priority",
    "effort_points",
    "cost_estimate",
    "risk_level",
    "sprint",
    "okr_alignment",
    "deliverable_type",
    "tags",
]


def ensure_project_files():
    """Ensure all required project files exist."""
    root = get_project_root()

    # Create charter if not exists
    charter_path = root / "00_project_charter.md"
    if not charter_path.exists():
        charter_path.write_text("""# Project Charter

## Goal
[Define the main objective of this project]

## Scope
[What's included and what's not]

## Success Criteria
[How will we know this project is successful?]

## Stakeholders
[Who are the key people involved?]
""", encoding="utf-8")

    # Create plan if not exists
    plan_path = root / "01_plan.json"
    if not plan_path.exists():
        save_plan(ProjectPlan(), log_changes=False)
    else:
        csv_path = root / "plan.csv"
        if not csv_path.exists():
            export_plan_to_csv(load_plan())

    # Create followups if not exists
    followups_path = root / "02_followups.json"
    if not followups_path.exists():
        followups_path.write_text("[]", encoding="utf-8")

    # Create risks/issues if not exists
    risks_path = root / "03_risks_issues.md"
    if not risks_path.exists():
        risks_path.write_text("# Risks & Issues\n\n", encoding="utf-8")

    # Create decisions log if not exists
    decisions_path = root / "04_decisions.log"
    if not decisions_path.exists():
        decisions_path.write_text("# Decision Log\n\n", encoding="utf-8")

    # Create focus list file
    focus_path = root / "05_focus.json"
    if not focus_path.exists():
        focus_path.write_text(json.dumps({
            "generated_at": None,
            "horizon_days": 14,
            "max_items": 10,
            "items": []
        }, indent=2), encoding="utf-8")

    # Create artifacts folder structure for easy reference
    artifacts_root = root / "artifacts"
    (artifacts_root / "analysis").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "documentation").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "inputs").mkdir(parents=True, exist_ok=True)
    (artifacts_root / "exports").mkdir(parents=True, exist_ok=True)
    readme_path = artifacts_root / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# Artifacts Archive\n\n"
            "Store supporting documents, exports, and generated reports here so the agent can reference them.\n"
            "- analysis/: Outputs such as Monte Carlo charts or CSVs\n"
            "- documentation/: Generated briefings and Word reports\n"
            "- inputs/: Raw materials supplied by stakeholders\n"
            "- exports/: Anything you share externally\n",
            encoding="utf-8",
        )

    # Create .pm directory for agent memory
    pm_dir = root / ".pm"
    pm_dir.mkdir(exist_ok=True)


def _parse_iso_date(label: str, value: Optional[str]) -> Optional[date]:
    """Parse ISO date strings with friendly errors."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {label} date '{value}'. Use YYYY-MM-DD and ensure the date exists."
        ) from exc


def _apply_actual_tracking(task: Task, previous_status: Optional[str] = None):
    """Auto-capture actual start/end dates based on status changes."""
    today = date.today()
    status = task.status

    if status in {"in_progress", "done"} and task.actual_start_date is None:
        task.actual_start_date = today

    if status == "done" and task.actual_end_date is None:
        task.actual_end_date = today


def _plan_from_data(data: List[dict]) -> ProjectPlan:
    """Create a ProjectPlan instance from JSON-compatible list."""
    tasks = []
    for task_data in data:
        if task_data.get("start_date"):
            task_data["start_date"] = date.fromisoformat(task_data["start_date"])
        if task_data.get("due_date"):
            task_data["due_date"] = date.fromisoformat(task_data["due_date"])
        if task_data.get("actual_start_date"):
            task_data["actual_start_date"] = date.fromisoformat(task_data["actual_start_date"])
        if task_data.get("actual_end_date"):
            task_data["actual_end_date"] = date.fromisoformat(task_data["actual_end_date"])
        tasks.append(Task(**task_data))
    return ProjectPlan(tasks=tasks)


def load_plan() -> ProjectPlan:
    """Load the project plan from 01_plan.json."""
    root = get_project_root()
    plan_path = root / "01_plan.json"

    if not plan_path.exists():
        return ProjectPlan()

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    return _plan_from_data(data)


def _summarize_plan_changes(before: ProjectPlan, after: ProjectPlan) -> Optional[str]:
    """Return a human-readable summary of plan differences."""
    before_map: Dict[int, Task] = {task.id: task for task in before.tasks}
    after_map: Dict[int, Task] = {task.id: task for task in after.tasks}

    added_ids = sorted(set(after_map) - set(before_map))
    removed_ids = sorted(set(before_map) - set(after_map))
    common_ids = sorted(set(before_map) & set(after_map))

    summary_lines = []

    for task_id in added_ids:
        task = after_map[task_id]
        due = task.due_date.isoformat() if task.due_date else "no due date"
        summary_lines.append(f"- Added #{task.id}: {task.title} (status {task.status}, due {due})")

    for task_id in removed_ids:
        task = before_map[task_id]
        summary_lines.append(f"- Removed #{task.id}: {task.title}")

    fields = [
        "title",
        "assignee",
        "start_date",
        "due_date",
        "status",
        "dependencies",
        "priority",
        "effort_points",
        "cost_estimate",
        "risk_level",
        "sprint",
        "okr_alignment",
        "deliverable_type",
        "tags",
    ]
    for task_id in common_ids:
        before_task = before_map[task_id]
        after_task = after_map[task_id]
        changes = []
        for field in fields:
            before_value = getattr(before_task, field)
            after_value = getattr(after_task, field)
            if before_value != after_value:
                if isinstance(before_value, date):
                    before_value = before_value.isoformat()
                if isinstance(after_value, date):
                    after_value = after_value.isoformat()
                changes.append(f"{field}: {before_value} -> {after_value}")
        if changes:
            summary = ", ".join(changes)
            summary_lines.append(f"- Updated #{task_id}: {summary}")

    if not summary_lines:
        return None

    header = f"Plan updated ({len(summary_lines)} change{'s' if len(summary_lines) != 1 else ''}):"
    return "\n".join([header] + summary_lines)


def save_plan(plan: ProjectPlan, log_changes: bool = True):
    """Save the project plan to 01_plan.json."""
    root = get_project_root()
    plan_path = root / "01_plan.json"

    before_plan = ProjectPlan()
    if plan_path.exists():
        before_plan = _plan_from_data(json.loads(plan_path.read_text(encoding="utf-8")))

    plan_path.write_text(json.dumps(plan.to_json_list(), indent=2), encoding="utf-8")
    export_plan_to_csv(plan)

    if log_changes:
        change_summary = _summarize_plan_changes(before_plan, plan)
        if change_summary:
            append_log(change_summary)


def _resolve_csv_path(csv_file: Optional[str] = None) -> Path:
    root = get_project_root()
    if csv_file:
        csv_path = Path(csv_file)
        if not csv_path.is_absolute():
            csv_path = root / csv_path
    else:
        csv_path = root / "plan.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    return csv_path


def export_plan_to_csv(plan: ProjectPlan, csv_file: Optional[str] = None):
    """Write the project plan to a CSV file for manual editing."""
    csv_path = _resolve_csv_path(csv_file)
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_CSV_FIELDS)
        writer.writeheader()
        for task in plan.tasks:
            writer.writerow({
                "id": task.id,
                "title": task.title,
                "assignee": task.assignee or "",
                "start_date": task.start_date.isoformat() if task.start_date else "",
                "due_date": task.due_date.isoformat() if task.due_date else "",
                "actual_start_date": task.actual_start_date.isoformat() if task.actual_start_date else "",
                "actual_end_date": task.actual_end_date.isoformat() if task.actual_end_date else "",
                "status": task.status,
                "dependencies": ",".join(str(dep) for dep in task.dependencies),
                "optimistic_days": task.optimistic_days or "",
                "likely_days": task.likely_days or "",
                "pessimistic_days": task.pessimistic_days or "",
                "priority": task.priority or "",
                "effort_points": task.effort_points if task.effort_points is not None else "",
                "cost_estimate": task.cost_estimate if task.cost_estimate is not None else "",
                "risk_level": task.risk_level or "",
                "sprint": task.sprint or "",
                "okr_alignment": task.okr_alignment or "",
                "deliverable_type": task.deliverable_type or "",
                "tags": ";".join(task.tags) if task.tags else "",
            })


def import_plan_from_csv(csv_file: Optional[str] = None) -> ProjectPlan:
    """Load plan data from a CSV file and return a ProjectPlan object."""
    csv_path = _resolve_csv_path(csv_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    def parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        value = value.strip()
        if not value:
            return None
        # Try ISO format first
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
        # Try common alternatives (e.g., MM/DD/YYYY)
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Invalid date format '{value}'. Use YYYY-MM-DD or MM/DD/YYYY.")

    def parse_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return int(float(value))

    def parse_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return float(value)

    tasks: List[Task] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row.get("id"):
                continue
            try:
                task_id = int(row["id"])
            except ValueError as exc:
                raise ValueError(f"Invalid task ID '{row['id']}' in CSV") from exc

            deps_str = (row.get("dependencies") or "").strip()
            dependencies: List[int] = []
            if deps_str:
                for dep in deps_str.split(","):
                    dep = dep.strip()
                    if dep:
                        try:
                            dependencies.append(int(dep))
                        except ValueError as exc:
                            raise ValueError(f"Invalid dependency '{dep}' for task {task_id}") from exc

            task = Task(
                id=task_id,
                title=row.get("title", "").strip() or f"Task {task_id}",
                assignee=row.get("assignee") or None,
                start_date=parse_date(row.get("start_date")),
                due_date=parse_date(row.get("due_date")),
                actual_start_date=parse_date(row.get("actual_start_date")),
                actual_end_date=parse_date(row.get("actual_end_date")),
                status=(row.get("status") or "todo").strip() or "todo",
                dependencies=dependencies,
                optimistic_days=parse_int(row.get("optimistic_days")),
                likely_days=parse_int(row.get("likely_days")),
                pessimistic_days=parse_int(row.get("pessimistic_days")),
                priority=(row.get("priority") or "").strip() or None,
                effort_points=parse_float(row.get("effort_points")),
                cost_estimate=parse_float(row.get("cost_estimate")),
                risk_level=(row.get("risk_level") or "").strip() or None,
                sprint=(row.get("sprint") or "").strip() or None,
                okr_alignment=(row.get("okr_alignment") or "").strip() or None,
                deliverable_type=(row.get("deliverable_type") or "").strip() or None,
                tags=[tag.strip() for tag in (row.get("tags") or "").split(";") if tag.strip()],
            )
            tasks.append(task)

    return ProjectPlan(tasks=tasks)


def load_focus_board() -> FocusBoard:
    """Load the focus board from 05_focus.json."""
    root = get_project_root()
    focus_path = root / "05_focus.json"

    if not focus_path.exists():
        return FocusBoard()

    data = json.loads(focus_path.read_text(encoding="utf-8"))

    generated_at = data.get("generated_at")
    if generated_at:
        data["generated_at"] = datetime.fromisoformat(generated_at)

    for item in data.get("items", []):
        if item.get("due_date"):
            item["due_date"] = date.fromisoformat(item["due_date"])

    return FocusBoard(**data)


def save_focus_board(board: FocusBoard):
    """Persist the focus board representation."""
    root = get_project_root()
    focus_path = root / "05_focus.json"
    focus_path.write_text(board.model_dump_json(indent=2), encoding="utf-8")


@tool
def read_context() -> str:
    """Read the project context including charter, recent logs, and plan summary."""
    root = get_project_root()

    # Read charter
    charter_path = root / "00_project_charter.md"
    charter = charter_path.read_text(encoding="utf-8") if charter_path.exists() else "No charter found."

    # Read plan summary
    plan = load_plan()
    plan_summary = f"Total tasks: {len(plan.tasks)}\n"
    for task in plan.tasks:
        plan_summary += f"  - [{task.status}] {task.title} (ID: {task.id})\n"

    # Read recent decisions
    decisions_path = root / "04_decisions.log"
    decisions = decisions_path.read_text(encoding="utf-8") if decisions_path.exists() else "No decisions logged."

    context = f"""# Project Context

## Charter
{charter}

## Plan Summary
{plan_summary}

## Recent Decisions
{decisions[-1000:]}
"""
    return context


@tool
def read_plan() -> str:
    """Read the raw project plan JSON."""
    plan = load_plan()
    return json.dumps(plan.to_json_list(), indent=2)


@tool
def add_task(
    title: str,
    assignee: Optional[str] = None,
    due_date: Optional[str] = None,
    start_date: Optional[str] = None,
    actual_start_date: Optional[str] = None,
    actual_end_date: Optional[str] = None,
    status: str = "todo",
    dependencies: Optional[List[int]] = None
) -> str:
    """Add a new task to the project plan.

    Args:
        title: The task title
        assignee: Person assigned to the task
        due_date: Due date in YYYY-MM-DD format
        start_date: Start date in YYYY-MM-DD format
        status: Task status (todo, in_progress, done, blocked)
        dependencies: List of task IDs this task depends on
        actual_start_date: Actual work start date (optional, YYYY-MM-DD)
        actual_end_date: Actual completion date (optional, YYYY-MM-DD)

    Returns:
        Confirmation message with the new task ID
    """
    plan = load_plan()

    task_id = plan.get_next_id()

    try:
        parsed_start = _parse_iso_date("start", start_date)
        parsed_due = _parse_iso_date("due", due_date)
        parsed_actual_start = _parse_iso_date("actual start", actual_start_date)
        parsed_actual_end = _parse_iso_date("actual end", actual_end_date)
    except ValueError as exc:
        return f"Error: {exc}"

    task = Task(
        id=task_id,
        title=title,
        assignee=assignee,
        start_date=parsed_start,
        due_date=parsed_due,
        actual_start_date=parsed_actual_start,
        actual_end_date=parsed_actual_end,
        status=status,
        dependencies=dependencies or []
    )

    _apply_actual_tracking(task)

    plan.tasks.append(task)
    save_plan(plan)

    # Auto-generate Gantt chart
    from .viz import generate_gantt
    generate_gantt()

    return f"Task #{task_id} '{title}' added successfully."


@tool
def update_task(
    task_id: int,
    title: Optional[str] = None,
    assignee: Optional[str] = None,
    due_date: Optional[str] = None,
    start_date: Optional[str] = None,
    actual_start_date: Optional[str] = None,
    actual_end_date: Optional[str] = None,
    status: Optional[str] = None,
    dependencies: Optional[List[int]] = None
) -> str:
    """Update an existing task.

    Args:
        task_id: ID of the task to update
        title: New title (optional)
        assignee: New assignee (optional)
        due_date: New due date in YYYY-MM-DD format (optional)
        start_date: New start date in YYYY-MM-DD format (optional)
        status: New status (optional)
        actual_start_date: Override actual start date (optional)
        actual_end_date: Override actual completion date (optional)
        dependencies: New dependencies list (optional)

    Returns:
        Confirmation message
    """
    plan = load_plan()
    task = plan.get_task(task_id)

    if not task:
        return f"Error: Task #{task_id} not found."

    if title is not None:
        task.title = title
    if assignee is not None:
        task.assignee = assignee

    previous_status = task.status

    try:
        if start_date is not None:
            task.start_date = _parse_iso_date("start", start_date)
        if due_date is not None:
            task.due_date = _parse_iso_date("due", due_date)
        if actual_start_date is not None:
            task.actual_start_date = _parse_iso_date("actual start", actual_start_date)
        if actual_end_date is not None:
            task.actual_end_date = _parse_iso_date("actual end", actual_end_date)
    except ValueError as exc:
        return f"Error: {exc}"
    if status is not None:
        task.status = status
    if dependencies is not None:
        task.dependencies = dependencies

    _apply_actual_tracking(task, previous_status=previous_status)

    save_plan(plan)

    # Auto-generate Gantt chart
    from .viz import generate_gantt
    generate_gantt()

    return f"Task #{task_id} updated successfully."

def append_log(content: str) -> str:
    """Append a timestamped entry to the decision log.

    Args:
        content: The log entry content

    Returns:
        Confirmation message
    """
    root = get_project_root()
    log_path = root / "04_decisions.log"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## {timestamp}\n{content}\n"

    with open(log_path, "a") as f:
        f.write(entry)

    return "Log entry added successfully."


append_log_tool = tool(append_log)


@tool
def write_file(filename: str, content: str) -> str:
    """Write content to a new file in the project directory.

    Can only create files in docs/, attachments/, or documentation.md.
    Cannot overwrite core project files.

    Args:
        filename: The filename to create
        content: The file content

    Returns:
        Confirmation message
    """
    root = get_project_root()
    file_path = root / filename

    # Security check - prevent overwriting core files
    protected_files = [
        "00_project_charter.md",
        "01_plan.json",
        "02_followups.json",
        "03_risks_issues.md",
        "04_decisions.log"
    ]

    if file_path.name in protected_files:
        return f"Error: Cannot overwrite protected file {filename}. Use specific tools instead."

    # Only allow certain directories
    if not (
        filename.startswith("docs/") or
        filename.startswith("attachments/") or
        filename == "documentation.md" or
        filename.startswith("05_meeting_notes/") or
        filename.startswith("artifacts/")
    ):
        return (
            "Error: Can only create files in docs/, attachments/, 05_meeting_notes/, "
            "artifacts/, or documentation.md"
        )

    # Create parent directories if needed
    file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(content, encoding="utf-8")
    return f"File {filename} created successfully."


@tool
def focus_overview(horizon_days: int = 14, limit: int = 10) -> str:
    """Generate (and persist) a near-term focus board summary."""
    plan = load_plan()
    if not plan.tasks:
        return "No tasks available to build a focus list."

    board = build_focus_board(plan, horizon_days=horizon_days, limit=limit)
    save_focus_board(board)

    if not board.items:
        return f"No tasks due within {horizon_days} days and no blocked work."

    lines = [
        f"Focus horizon: {board.horizon_days} days | generated at {board.generated_at}",
        "Items:"
    ]
    for item in board.items:
        status = item.status or "unknown"
        due = item.due_date.isoformat() if item.due_date else "no due date"
        reason = item.reason.replace("_", " ")
        days = f"{item.days_remaining}d" if item.days_remaining is not None else "n/a"
        lines.append(
            f"- #{item.task_id or '?'} {item.title} ({status}) due {due} | "
            f"{reason} | {days} remaining"
        )

    return "\n".join(lines)


@tool
def critical_path_snapshot() -> str:
    """Return a textual critical path analysis."""
    plan = load_plan()
    if not plan.tasks:
        return "No tasks defined for critical path analysis."

    result = compute_critical_path(plan)
    if not result.tasks:
        return "Unable to compute critical path (check dependencies)."

    lines = [
        f"Critical path length: {result.total_duration} days starting {result.project_start}"
    ]
    for entry in result.tasks:
        marker = "CRITICAL" if entry.is_critical else f"Slack {entry.slack}d"
        lines.append(
            f"- #{entry.task_id} {entry.title} | {marker} | "
            f"ES {entry.earliest_start} -> EF {entry.earliest_finish}"
        )

    if result.warnings:
        lines.append("\nWarnings:")
        lines.extend(f"* {w}" for w in result.warnings)

    return "\n".join(lines)


@tool
def schedule_risk_report(iterations: int = 2000) -> str:
    """Run a Monte Carlo schedule risk simulation."""
    plan = load_plan()
    if not plan.tasks:
        return "No tasks defined to run Monte Carlo schedule analysis."

    result = run_monte_carlo_simulation(plan, iterations=iterations)
    percentiles = result.finish_percentiles
    lines = [
        f"Monte Carlo (n={iterations}) shows:",
        f"- P10 completion: {percentiles['p10']}",
        f"- P50 completion: {percentiles['p50']}",
        f"- P90 completion: {percentiles['p90']}",
    ]

    if result.risk_drivers:
        lines.append("\nTop risk drivers:")
        for driver in result.risk_drivers[:5]:
            lines.append(
                f"- #{driver.task_id} {driver.title} | corr {driver.correlation:.2f} | "
                f"avg duration {driver.avg_duration:.1f}d"
            )

    return "\n".join(lines)


@tool
def list_project_artifacts(max_items: int = 15) -> str:
    """List recent files in the artifacts folder for quick reference."""
    root = get_project_root()
    artifacts = summarize_artifacts(root, max_items=max_items)

    if not artifacts:
        return "No artifacts stored yet."

    lines = ["Artifacts catalog:"]
    for art in artifacts:
        lines.append(
            f"- {art.path} ({art.size_kb:.1f} KB, updated {art.modified.isoformat()})"
        )

    return "\n".join(lines)


# Export all tools
TOOLS = [
    read_context,
    read_plan,
    add_task,
    update_task,
    append_log_tool,
    write_file,
    focus_overview,
    critical_path_snapshot,
    schedule_risk_report,
    list_project_artifacts,
]

# ---------- FILE: viz.py ----------
"""Visualization tools for generating Gantt charts."""

import warnings
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta, date
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from .analysis import MonteCarloResult
from .tools import load_plan, get_project_root

# Suppress matplotlib UserWarnings about tick generation
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')


def generate_gantt():
    """Generate a Gantt chart from the current plan and save it as PNG."""
    plan = load_plan()
    root = get_project_root()

    if not plan.tasks:
        print("No tasks in plan - skipping Gantt generation")
        return

    # Prepare data for plotting
    tasks_data = []
    for task in plan.tasks:
        # Use today as start if not specified
        start = task.start_date or date.today()

        # Use start + 1 day as due if not specified
        due = task.due_date or (start + timedelta(days=1))

        tasks_data.append({
            'Task': f"#{task.id} {task.title[:30]}",
            'Start': start,
            'End': due,
            'Status': task.status,
            'Assignee': task.assignee or "Unassigned",
            'ActualStart': task.actual_start_date,
            'ActualEnd': task.actual_end_date,
        })

    df = pd.DataFrame(tasks_data)

    # Create figure
    fig, ax = plt.subplots(figsize=(12, max(6, len(plan.tasks) * 0.4)))

    # Color mapping for status
    colors = {
        'todo': '#95a5a6',
        'in_progress': '#3498db',
        'done': '#2ecc71',
        'blocked': '#e74c3c'
    }

    # Plot each task
    actual_label_added = False

    for idx, task in df.iterrows():
        start_date = task['Start']
        end_date = task['End']
        duration = (end_date - start_date).days

        color = colors.get(task['Status'], '#95a5a6')

        ax.barh(
            idx,
            duration,
            left=mdates.date2num(start_date),
            height=0.6,
            color=color,
            alpha=0.8,
            label=task['Status'] if task['Status'] not in ax.get_legend_handles_labels()[1] else ""
        )

        # Add assignee label if exists
        if task['Assignee'] != "Unassigned":
            ax.text(
                mdates.date2num(start_date) + duration / 2,
                idx,
                task['Assignee'],
                ha='center',
                va='center',
                color='white',
                fontsize=8,
                weight='bold'
            )

        actual_start = task['ActualStart']
        actual_end = task['ActualEnd']
        if pd.notna(actual_start) and pd.notna(actual_end):
            if isinstance(actual_start, pd.Timestamp):
                actual_start = actual_start.date()
            if isinstance(actual_end, pd.Timestamp):
                actual_end = actual_end.date()
        if isinstance(actual_start, date) and isinstance(actual_end, date):
            actual_duration = (actual_end - actual_start).days
            if actual_duration <= 0:
                actual_duration = 1
            ax.barh(
                idx,
                actual_duration,
                left=mdates.date2num(actual_start),
                height=0.3,
                color='none',
                edgecolor='#f39c12',
                linewidth=2,
                label="Actual" if not actual_label_added else ""
            )
            actual_label_added = True

    # Format axes
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels(df['Task'])
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(plan.tasks) // 10)))

    plt.xticks(rotation=45, ha='right')
    ax.set_xlabel('Date')
    ax.set_title('Project Gantt Chart', fontsize=14, weight='bold')

    # Add legend
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, loc='upper right')

    # Grid
    ax.grid(True, alpha=0.3, axis='x')

    # Tight layout
    plt.tight_layout()

    # Save
    output_path = root / "gantt_latest.png"
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Gantt chart saved to {output_path}")


def render_text_gantt(console: Console = None):
    """Render a text-based Gantt chart in the terminal using Rich."""
    if console is None:
        console = Console()

    plan = load_plan()

    if not plan.tasks:
        console.print("[yellow]No tasks in plan to display[/yellow]")
        return

    # Calculate date range
    all_dates = []
    for task in plan.tasks:
        start = task.start_date or date.today()
        due = task.due_date or (start + timedelta(days=1))
        all_dates.extend([start, due])

    min_date = min(all_dates)
    max_date = max(all_dates)
    total_days = (max_date - min_date).days + 1

    # Create timeline header
    table = Table(title="Project Gantt Chart (Timeline View)", show_header=True, header_style="bold cyan")

    # Add columns
    table.add_column("ID", style="cyan", width=4, no_wrap=True)
    table.add_column("Task", style="white", width=30, no_wrap=False)
    table.add_column("Status", style="magenta", width=12)
    table.add_column("Timeline", style="white", width=max(40, min(80, total_days)), no_wrap=True)
    table.add_column("Duration", style="yellow", width=10)

    # Status colors and symbols
    status_styles = {
        'todo': ('[ ]', 'dim white'),
        'in_progress': ('[>]', 'blue'),
        'done': ('[X]', 'green'),
        'blocked': ('[!]', 'red')
    }

    # Render each task
    for task in plan.tasks:
        start = task.start_date or date.today()
        due = task.due_date or (start + timedelta(days=1))
        duration = (due - start).days + 1

        # Calculate position in timeline
        days_from_start = (start - min_date).days

        # Create visual timeline bar
        bar_width = max(40, min(80, total_days))
        if total_days <= bar_width:
            # 1:1 scale
            bar_start = days_from_start
            bar_length = duration
        else:
            # Scaled down
            scale = bar_width / total_days
            bar_start = int(days_from_start * scale)
            bar_length = max(1, int(duration * scale))

        # Build the timeline visualization
        timeline = [' '] * bar_width

        # Fill in the bar
        status_char = '=' if task.status == 'done' else '>' if task.status == 'in_progress' else '-' if task.status == 'todo' else '!'
        for i in range(bar_start, min(bar_start + bar_length, bar_width)):
            timeline[i] = status_char

        # Color the timeline based on status
        timeline_str = ''.join(timeline)
        status_symbol, status_color = status_styles.get(task.status, ('[ ]', 'white'))

        # Format dates
        date_range = f"{duration}d"

        # Add row
        table.add_row(
            str(task.id),
            task.title[:30],
            f"{status_symbol} {task.status}",
            f"[{status_color}]{timeline_str}[/{status_color}]",
            date_range
        )

    # Print the table
    console.print()
    console.print(table)
    console.print()

    # Print legend
    console.print("[bold]Legend:[/bold]")
    console.print("  [dim white]- - -[/dim white] Todo    [blue]> > >[/blue] In Progress    [green]= = =[/green] Done    [red]! ! ![/red] Blocked")
    console.print()

    # Print date range
    console.print(f"[dim]Timeline: {min_date.isoformat()} to {max_date.isoformat()} ({total_days} days)[/dim]")
    console.print()


def save_finish_distribution_plot(
    result: MonteCarloResult,
    output_path: Path,
    bins: int = 40,
) -> bool:
    """Persist a histogram of Monte Carlo completion dates."""
    if result.finish_samples is None or len(result.finish_samples) == 0:
        return False

    # Convert sampled finish offsets into actual dates for plotting
    finish_days = np.array(result.finish_samples, dtype=float)
    finish_dates = [
        result.project_start + timedelta(days=float(day))
        for day in finish_days
    ]
    finish_nums = mdates.date2num(finish_dates)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(finish_nums, bins=bins, color="#1abc9c", alpha=0.85)
    ax.set_title("Monte Carlo Completion Distribution")
    ax.set_xlabel("Completion date")
    ax.set_ylabel("Simulations")
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()

    # Add percentile markers
    for label, color in (("p10", "#3498db"), ("p50", "#f1c40f"), ("p90", "#e74c3c")):
        percentile_date = result.finish_percentiles.get(label)
        if percentile_date:
            ax.axvline(
                mdates.date2num(percentile_date),
                color=color,
                linestyle="--",
                linewidth=1.2,
                label=label.upper(),
            )

    if ax.get_legend_handles_labels()[0]:
        ax.legend()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def save_risk_driver_plot(result: MonteCarloResult, output_path: Path, top_n: int = 8) -> bool:
    """Persist a bar chart highlighting the top risk drivers."""
    drivers = result.risk_drivers[:top_n]
    if not drivers:
        return False

    titles = [f"#{driver.task_id}" for driver in drivers]
    correlations = [driver.correlation for driver in drivers]
    colors = ["#e74c3c" if corr > 0 else "#3498db" for corr in correlations]

    fig, ax = plt.subplots(figsize=(8, max(4, len(drivers) * 0.6)))
    y_pos = np.arange(len(drivers))
    ax.barh(y_pos, correlations, color=colors, alpha=0.85)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(titles)
    ax.set_xlabel("Correlation to project finish")
    ax.set_title("Top Risk Drivers")
    ax.axvline(0, color="#7f8c8d", linewidth=0.8)

    for idx, driver in enumerate(drivers):
        ax.text(
            correlations[idx],
            idx,
            f"{driver.avg_duration:.1f}d",
            va="center",
            ha="left" if correlations[idx] >= 0 else "right",
            fontsize=8,
            color="#2c3e50",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def save_cost_distribution_plot(
    result: MonteCarloResult,
    output_path: Path,
    bins: int = 40,
    currency: str = "$",
) -> bool:
    """Persist a histogram of projected costs if data exists."""
    if result.cost_samples is None or len(result.cost_samples) == 0:
        return False

    costs = np.array(result.cost_samples, dtype=float)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(costs, bins=bins, color="#9b59b6", alpha=0.85)
    ax.set_title("Cost Exposure Distribution")
    ax.set_xlabel(f"Total cost ({currency})")
    ax.set_ylabel("Simulations")

    for label, color in (("p10", "#3498db"), ("p50", "#f1c40f"), ("p90", "#e74c3c")):
        value = (result.cost_percentiles or {}).get(label)
        if value is not None:
            ax.axvline(value, color=color, linestyle="--", linewidth=1.2, label=label.upper())

    if ax.get_legend_handles_labels()[0]:
        ax.legend()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def save_burnup_chart(series: list, output_path: Path) -> bool:
    """Plot burnup (done vs total scope) over time."""
    if not series:
        return False

    dates = [point["date"] for point in series]
    done = [point["done"] for point in series]
    total = [point["total"] for point in series]

    fig, ax = plt.subplots(figsize=(10, 5))
    x = mdates.date2num(dates)
    ax.plot_date(x, done, "-o", label="Done", color="#2ecc71")
    ax.plot_date(x, total, "-o", label="Scope", color="#34495e")
    ax.fill_between(x, done, total, color="#ecf0f1", alpha=0.4)
    ax.set_title("Burnup (Done vs Scope)")
    ax.set_ylabel("Tasks")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()
    ax.legend()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def save_throughput_chart(series: list, output_path: Path) -> bool:
    """Plot completed tasks per day."""
    if not series:
        return False

    dates = [point["date"] for point in series]
    counts = [point["completed"] for point in series]
    x = mdates.date2num(dates)

    fig, ax = plt.subplots(figsize=(10, 4))
    width = 0.8
    ax.bar(x, counts, width=width, color="#3498db", alpha=0.85)
    ax.set_title("Throughput (Completed / Day)")
    ax.set_ylabel("Tasks")
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def save_slip_histogram(records: list, output_path: Path) -> bool:
    """Plot planned vs actual slip distribution."""
    if not records:
        return False

    values = [record["slip_days"] for record in records]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(values, bins=30, color="#e67e22", alpha=0.85)
    ax.axvline(0, color="#2c3e50", linestyle="--", linewidth=1.2)
    ax.set_title("Planned vs Actual (days late/early)")
    ax.set_xlabel("Days late (+) / early (-)")
    ax.set_ylabel("Tasks")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True

