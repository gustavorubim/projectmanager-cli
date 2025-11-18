"""CLI interface for the PM Agent using Typer."""

import os
import sys
import json
import subprocess
import time
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
from .tools import load_plan, ensure_project_files, get_project_root
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


def parse_slash_command(user_input: str):
    """Parse slash command and execute it."""
    # Remove leading slash and split into parts
    parts = user_input[1:].split()

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


if __name__ == "__main__":
    app()
