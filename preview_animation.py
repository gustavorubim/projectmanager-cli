"""Preview the PM Agent startup animation."""

import time
from rich.console import Console
from rich.text import Text
from rich.align import Align
from rich.panel import Panel

console = Console()

def show_unicode_version():
    """Show the Unicode block character version."""
    LOGO = """
   ██████╗ ███╗   ███╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗
   ██╔══██╗████╗ ████║    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
   ██████╔╝██╔████╔██║    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
   ██╔═══╝ ██║╚██╔╝██║    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
   ██║     ██║ ╚═╝ ██║    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
   ╚═╝     ╚═╝     ╚═╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""
    MASCOT = """
        ╔═══════════╗
        ║ ✓ Tasks   ║
        ║ ✓ Plans   ║
        ║ ○ Goals   ║
        ╚═══════════╝
           ║   ║
           ╚═══╝
"""
    console.print(Panel("[bold cyan]Unicode Version (Prettier)[/bold cyan]", border_style="cyan"))
    text = Text()
    text.append(LOGO, style="bold bright_cyan")
    text.append("\n")
    text.append(MASCOT, style="magenta")
    text.append("\n\n")
    text.append("        AI-Powered Project Manager", style="bold cyan")
    console.print(Align.center(text))
    console.print()


def show_ascii_version():
    """Show the ASCII fallback version."""
    LOGO = r"""
    ____  __  __      _    ____ _____ _   _ _____
   |  _ \|  \/  |    / \  / ___| ____| \ | |_   _|
   | |_) | |\/| |   / _ \| |  _|  _| |  \| | | |
   |  __/| |  | |  / ___ \ |_| | |___| |\  | | |
   |_|   |_|  |_| /_/   \_\____|_____|_| \_| |_|
"""
    MASCOT = """
        +===========+
        | [ ] Tasks |
        | [x] Plans |
        | [ ] Goals |
        +===========+
           |   |
           +===+
"""
    console.print(Panel("[bold green]ASCII Version (Windows Compatible)[/bold green]", border_style="green"))
    text = Text()
    text.append(LOGO, style="bold bright_cyan")
    text.append("\n")
    text.append(MASCOT, style="magenta")
    text.append("\n\n")
    text.append("        AI-Powered Project Manager", style="bold cyan")
    console.print(Align.center(text))
    console.print()


if __name__ == "__main__":
    console.print("\n[bold yellow]PM Agent - Startup Animation Preview[/bold yellow]\n")

    try:
        show_unicode_version()
        time.sleep(1)
        show_ascii_version()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

    console.print("\n[dim]The actual animation fades in gradually and is transient (disappears after playing)[/dim]\n")
