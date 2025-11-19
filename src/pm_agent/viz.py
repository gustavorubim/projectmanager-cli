"""Visualization tools for generating Gantt charts."""

import warnings
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
            'Assignee': task.assignee or "Unassigned"
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
