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
    summary_path.write_text(json.dumps(summary_payload, indent=2))
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
        summary_path.write_text(json.dumps(summary, indent=2))

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
        summary_path.write_text(json.dumps(summary, indent=2))
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
