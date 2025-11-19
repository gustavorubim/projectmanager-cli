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
