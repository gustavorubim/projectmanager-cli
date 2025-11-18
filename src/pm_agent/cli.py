"""CLI interface for the PM Agent using Typer."""

import os
import sys
import json
import subprocess
import time
import shlex
from pathlib import Path
from typing import Optional
from datetime import datetime

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

from .agent import create_agent, run_agent
from .analysis import (
    build_focus_board,
    compute_critical_path,
    run_monte_carlo_simulation,
    summarize_artifacts,
)
from .reporting import generate_documentation_report
from .tools import (
    load_plan,
    ensure_project_files,
    get_project_root,
    load_focus_board,
    save_focus_board,
)
from .viz import generate_gantt, render_text_gantt

app = typer.Typer(help="CLI Project Manager Agent", invoke_without_command=True)
console = Console()


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
    console.print("  /gantt               Display Gantt chart in CLI + save PNG")
    console.print("  /gantt --open        Display chart + save PNG + open file")
    console.print("  /followup <task>     Quick add task")
    console.print("    [dim]Example: /followup \"Fix bug\" --due 2025-12-01 --assign john[/dim]")
    console.print("  /criticalpath        Run critical path analysis")
    console.print("  /risk                Run Monte Carlo schedule risk simulation")
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
            task.due_date.isoformat() if task.due_date else "No due date"
        )

    console.print(table)
    console.print(f"\nTotal tasks: {len(plan.tasks)}")


def handle_plan_command(args: list):
    """Handle /plan command."""
    edit_mode = "--edit" in args
    plan_path = get_project_root() / "01_plan.json"

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


def render_risk_simulation(iterations: int = 2000):
    """Run Monte Carlo schedule risk simulation and display results."""
    plan = load_plan()

    if not plan.tasks:
        console.print("[yellow]No tasks found. Add tasks before running risk simulation.[/yellow]")
        return

    with console.status("[cyan]Running Monte Carlo simulation...[/cyan]"):
        result = run_monte_carlo_simulation(plan, iterations=iterations)

    percentiles = result.finish_percentiles
    panel_text = (
        f"P10 completion: {percentiles['p10']}\n"
        f"P50 completion: {percentiles['p50']}\n"
        f"P90 completion: {percentiles['p90']}\n"
        f"Iterations: {iterations}"
    )
    console.print(Panel(panel_text, title="Schedule Risk", border_style="cyan"))

    if result.risk_drivers:
        table = Table(title="Top Risk Drivers")
        table.add_column("ID", style="cyan")
        table.add_column("Task", style="white")
        table.add_column("Correlation", style="magenta", justify="right")
        table.add_column("Avg Duration (d)", style="yellow", justify="right")

        for driver in result.risk_drivers[:5]:
            table.add_row(
                str(driver.task_id),
                driver.title,
                f"{driver.correlation:.2f}",
                f"{driver.avg_duration:.1f}"
            )

        console.print(table)


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

    name = args[0]
    project_path = Path(name)

    if project_path.exists():
        console.print(f"[red]Error: Directory '{name}' already exists[/red]")
        return

    # Create project directory
    project_path.mkdir(parents=True)

    # Change to project directory
    os.chdir(project_path)

    # Create all standard files
    ensure_project_files()

    console.print(f"[green]OK[/green] Project '{name}' created successfully!")
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
            # Get user input
            user_input = console.input("[bold cyan]>[/bold cyan] ")

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
def plan(edit: bool = typer.Option(False, "--edit", help="Edit plan in editor")):
    """Show or edit the project plan."""
    args = ["--edit"] if edit else []
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
