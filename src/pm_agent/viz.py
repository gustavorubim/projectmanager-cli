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
