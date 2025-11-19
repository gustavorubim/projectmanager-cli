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


def _plan_from_data(data: List[dict]) -> ProjectPlan:
    """Create a ProjectPlan instance from JSON-compatible list."""
    tasks = []
    for task_data in data:
        if task_data.get("start_date"):
            task_data["start_date"] = date.fromisoformat(task_data["start_date"])
        if task_data.get("due_date"):
            task_data["due_date"] = date.fromisoformat(task_data["due_date"])
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

    try:
        parsed_start = _parse_iso_date("start", start_date)
        parsed_due = _parse_iso_date("due", due_date)
    except ValueError as exc:
        return f"Error: {exc}"

    task = Task(
        id=task_id,
        title=title,
        assignee=assignee,
        start_date=parsed_start,
        due_date=parsed_due,
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
    try:
        if start_date is not None:
            task.start_date = _parse_iso_date("start", start_date)
        if due_date is not None:
            task.due_date = _parse_iso_date("due", due_date)
    except ValueError as exc:
        return f"Error: {exc}"
    if status is not None:
        task.status = status
    if dependencies is not None:
        task.dependencies = dependencies

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
