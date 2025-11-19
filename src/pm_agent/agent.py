"""LangGraph agent implementation for the PM Agent."""

import os
import sqlite3
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
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

    if api_key:
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
            google_api_key=api_key,
        )
    else:
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temperature,
        )
    llm_with_tools = llm.bind_tools(TOOLS)

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

    def _normalize_ai_response(message: AIMessage) -> AIMessage:
        """Gemini can return list-based content; flatten to plain text for memory."""
        normalized = _ensure_text_message(message)
        return normalized

    # Define the agent reasoning node
    def call_model(state: MessagesState):
        """Call the LLM with the current state."""
        messages = [_ensure_text_message(msg) for msg in state["messages"]]

        # Add system message if not present
        if not any(isinstance(msg, SystemMessage) for msg in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

        response = llm_with_tools.invoke(messages)
        if isinstance(response, AIMessage):
            response = _normalize_ai_response(response)
        return {"messages": [response]}

    # Define tool execution node
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

        # Ensure we respond to every tool call, even if something goes wrong
        for tool_call in tool_calls:
            try:
                # LangChain providers may return dicts or objects; support both
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
                    import json as _json
                    try:
                        tool_args = _json.loads(tool_args)
                    except Exception:
                        tool_args = {"input": tool_args}

                tool = next((t for t in TOOLS if t.name == tool_name), None)
                if not tool:
                    tool_results.append(
                        ToolMessage(
                            content=f"Error: Unknown tool '{tool_name}'",
                            tool_call_id=tool_call_id
                        )
                    )
                    continue

                try:
                    result = tool.invoke(tool_args)
                    tool_results.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call_id
                        )
                    )
                except Exception as e:
                    # Log the error and continue with other tool calls
                    import traceback
                    error_msg = f"Error executing {tool_name}: {str(e)}\n{traceback.format_exc()}"
                    print(f"Tool execution error: {error_msg}")
                    tool_results.append(
                        ToolMessage(
                            content=f"Error: {str(e)}",
                            tool_call_id=tool_call_id
                        )
                    )
            except Exception as e:
                # Catch any error in extracting tool call info
                import traceback
                error_msg = f"Error processing tool call: {str(e)}\n{traceback.format_exc()}"
                print(f"Tool call processing error: {error_msg}")
                # Try to get tool_call_id even if other extraction failed
                try:
                    tool_call_id = getattr(tool_call, "id", None) or tool_call.get("id", "unknown")
                    tool_results.append(
                        ToolMessage(
                            content=f"Error processing tool call: {str(e)}",
                            tool_call_id=tool_call_id
                        )
                    )
                except:
                    # If we can't even get the ID, skip this one
                    pass

        # Verify we have a response for every tool call
        if len(tool_results) != len(tool_calls):
            print(f"WARNING: Tool call count mismatch. Calls: {len(tool_calls)}, Results: {len(tool_results)}")

        return {"messages": tool_results}

    # Define routing logic
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
    from langchain_core.messages import HumanMessage

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
