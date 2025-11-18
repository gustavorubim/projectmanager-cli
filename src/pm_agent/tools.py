"""Tools for reading and writing project files."""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List
from langchain_core.tools import tool

from .schema import ProjectPlan, Task


def get_project_root() -> Path:
    """Get the current project root directory."""
    return Path.cwd()


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
""")

    # Create plan if not exists
    plan_path = root / "01_plan.json"
    if not plan_path.exists():
        plan_path.write_text("[]")

    # Create followups if not exists
    followups_path = root / "02_followups.json"
    if not followups_path.exists():
        followups_path.write_text("[]")

    # Create risks/issues if not exists
    risks_path = root / "03_risks_issues.md"
    if not risks_path.exists():
        risks_path.write_text("# Risks & Issues\n\n")

    # Create decisions log if not exists
    decisions_path = root / "04_decisions.log"
    if not decisions_path.exists():
        decisions_path.write_text("# Decision Log\n\n")

    # Create .pm directory for agent memory
    pm_dir = root / ".pm"
    pm_dir.mkdir(exist_ok=True)


def load_plan() -> ProjectPlan:
    """Load the project plan from 01_plan.json."""
    root = get_project_root()
    plan_path = root / "01_plan.json"

    if not plan_path.exists():
        return ProjectPlan()

    data = json.loads(plan_path.read_text())

    # Convert date strings to date objects
    tasks = []
    for task_data in data:
        if task_data.get("start_date"):
            task_data["start_date"] = date.fromisoformat(task_data["start_date"])
        if task_data.get("due_date"):
            task_data["due_date"] = date.fromisoformat(task_data["due_date"])
        tasks.append(Task(**task_data))

    return ProjectPlan(tasks=tasks)


def save_plan(plan: ProjectPlan):
    """Save the project plan to 01_plan.json."""
    root = get_project_root()
    plan_path = root / "01_plan.json"

    plan_path.write_text(json.dumps(plan.to_json_list(), indent=2))


@tool
def read_context() -> str:
    """Read the project context including charter, recent logs, and plan summary."""
    root = get_project_root()

    # Read charter
    charter_path = root / "00_project_charter.md"
    charter = charter_path.read_text() if charter_path.exists() else "No charter found."

    # Read plan summary
    plan = load_plan()
    plan_summary = f"Total tasks: {len(plan.tasks)}\n"
    for task in plan.tasks:
        plan_summary += f"  - [{task.status}] {task.title} (ID: {task.id})\n"

    # Read recent decisions
    decisions_path = root / "04_decisions.log"
    decisions = decisions_path.read_text() if decisions_path.exists() else "No decisions logged."

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

    Returns:
        Confirmation message with the new task ID
    """
    plan = load_plan()

    task_id = plan.get_next_id()

    task = Task(
        id=task_id,
        title=title,
        assignee=assignee,
        start_date=date.fromisoformat(start_date) if start_date else None,
        due_date=date.fromisoformat(due_date) if due_date else None,
        status=status,
        dependencies=dependencies or []
    )

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
    if start_date is not None:
        task.start_date = date.fromisoformat(start_date)
    if due_date is not None:
        task.due_date = date.fromisoformat(due_date)
    if status is not None:
        task.status = status
    if dependencies is not None:
        task.dependencies = dependencies

    save_plan(plan)

    # Auto-generate Gantt chart
    from .viz import generate_gantt
    generate_gantt()

    return f"Task #{task_id} updated successfully."


@tool
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
        filename.startswith("05_meeting_notes/")
    ):
        return f"Error: Can only create files in docs/, attachments/, 05_meeting_notes/, or documentation.md"

    # Create parent directories if needed
    file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(content)
    return f"File {filename} created successfully."


# Export all tools
TOOLS = [
    read_context,
    read_plan,
    add_task,
    update_task,
    append_log,
    write_file
]
