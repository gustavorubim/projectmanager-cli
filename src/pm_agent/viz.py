"""Visualization tools for generating Gantt charts."""

import warnings
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd
from rich.console import Console
from rich.table import Table

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
