# PM Agent - CLI Project Manager

An intelligent CLI tool for managing projects using AI-powered automation with LangGraph and GPT-4.

## Features

- 🤖 AI-powered project management assistant
- 📋 Structured task tracking with JSON-based plans
- 📊 Automatic Gantt chart generation
- 💬 Interactive chat interface
- 📝 Decision logging and documentation
- 🔄 Persistent conversation memory

## Quick Install (TL;DR)

```bash
# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh  # macOS/Linux
# OR: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows

# Setup project
uv venv && source .venv/bin/activate  # macOS/Linux
# OR: uv venv && .venv\Scripts\activate  # Windows
uv pip install -e .

# Configure
cp .env.example .env  # Then add your OPENAI_API_KEY

# Start using
pm new my-project
cd my-project
pm chat
```

## Installation

### Prerequisites

**Install UV** (recommended - fast Python package manager):

```bash
# On macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Alternatively, you can use pip:
```bash
pip install uv
```

### Setup with UV (Recommended)

1. **Clone or navigate to this directory**

2. **Create a virtual environment and install dependencies**
```bash
# Create virtual environment
uv venv

# Activate the virtual environment
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# Install the package in editable mode with all dependencies
uv pip install -e .
```

3. **Set up your OpenAI API key**
```bash
# On macOS/Linux:
cp .env.example .env

# On Windows:
copy .env.example .env

# Edit .env and add your OpenAI API key
# OPENAI_API_KEY=sk-your-key-here
```

### Alternative: Setup with pip

If you prefer using pip instead of UV:

```bash
# Create virtual environment
python -m venv .venv

# Activate it
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

# Install dependencies
pip install -e .
```

## Quick Start

### 1. Start PM Agent
```bash
cd my-project-folder  # Or create new project with: pm new my-project
pm
```

You'll enter an interactive REPL where you can:
- **Chat naturally** with the AI: Just type your message
- **Use slash commands**: Type `/help` to see all commands

This creates a project with the following structure:
```
my-project/
├── 00_project_charter.md      # Goals, scope, stakeholders
├── 01_plan.json                # Task list (managed by agent)
├── 02_followups.json           # Action items
├── 03_risks_issues.md          # Risks and issues log
├── 04_decisions.log            # Decision journal
├── gantt_latest.png            # Auto-generated timeline
└── .pm/                        # Agent memory (SQLite)
```

### 2. Chat with the agent
```bash
pm chat
```

Example conversation:
```
You: Let's plan a website redesign. We need to finish by end of month.
Agent: I'll help you plan this. Let me create some initial tasks...
```

### 3. View project status
```bash
pm status
```

Shows a table of all tasks with their status, assignee, and due dates.

### 4. View/Edit the plan
```bash
pm plan              # View the plan JSON
pm plan --edit       # Edit in your default editor
```

### 5. Generate Gantt chart
```bash
pm gantt             # Generate chart
pm gantt --open      # Generate and open in viewer
```

## Commands Reference

### Project Management
- `pm new <name>` - Create a new project
- `pm status` - Show task table
- `pm status --week` - Get AI-generated weekly summary
- `pm plan` - View the plan JSON
- `pm plan --edit` - Edit plan in editor (auto-updates Gantt)

### Agent Interaction
- `pm chat` - Start interactive chat (default: current project)
- `pm chat <project>` - Chat about a specific project
- `pm chat --thread <name>` - Use a separate conversation thread
- `pm followup "task" --due 2025-12-01 --assign john` - Quick task add

### Visualization
- `pm gantt` - Generate Gantt chart
- `pm gantt --open` - Generate and open chart

### Utilities
- `pm archive` - Archive the current project to `_archive/`

## Project File Structure

### Core Files (Managed by Agent)
- **00_project_charter.md** - High-level goals and scope
- **01_plan.json** - Source of truth for tasks
- **04_decisions.log** - Chronological decision journal

### Task Schema
Each task in `01_plan.json` has:
```json
{
  "id": 1,
  "title": "Design Database",
  "assignee": "Alice",
  "start_date": "2025-10-01",
  "due_date": "2025-10-05",
  "status": "done",
  "dependencies": []
}
```

Status values: `todo`, `in_progress`, `done`, `blocked`

## Advanced Usage

### Multiple Conversation Threads
```bash
pm chat --thread backlog    # Separate brainstorming context
pm chat --thread planning   # Planning discussions
pm chat                     # Main project thread
```

### Weekly Status Reports
```bash
pm status --week
```
Generates an AI summary of progress, great for stand-ups or reports.

### Custom Documentation
The agent can create files in specific directories:
- `docs/` - General documentation
- `attachments/` - PDFs, images, exports
- `05_meeting_notes/` - Meeting notes
- `documentation.md` - Main living documentation

## Environment Variables

- `OPENAI_API_KEY` - Your OpenAI API key (required)
- `EDITOR` - Preferred text editor for `pm plan --edit` (default: notepad/nano)

## Tips

1. **Start with the charter** - Edit `00_project_charter.md` to define your project goals
2. **Use natural language** - The agent understands requests like "add a task for deploying to production next Friday"
3. **Check the Gantt** - Run `pm gantt --open` after major changes to visualize timeline
4. **Separate threads** - Use `--thread` for different conversation contexts
5. **Manual edits** - You can edit `01_plan.json` directly if needed, then run `pm gantt` to update the chart

## Architecture

Built with:
- **LangGraph** - Agent orchestration and state management
- **GPT-4** - Language model (gpt-4o)
- **Typer** - CLI framework
- **Rich** - Terminal UI
- **Matplotlib** - Gantt chart generation
- **SQLite** - Conversation memory persistence

## Security & Privacy

- All project files stay local on your machine
- Only text prompts are sent to OpenAI API
- Conversation history stored in `.pm/state.sqlite`
- Delete `.pm/` folder to clear memory while keeping project files

## Troubleshooting

### "OpenAIError: The api_key client option must be set"
This means your OpenAI API key is not configured:

1. **Check if `.env` file exists** in the project root (where `pyproject.toml` is)
2. **Create it from the example**:
   ```bash
   cp .env.example .env  # or 'copy' on Windows
   ```
3. **Edit `.env` and add your key**:
   ```
   OPENAI_API_KEY=sk-your-actual-key-here
   ```
4. **Restart your terminal** or reactivate your virtual environment

### "No module named 'pm_agent'"
Make sure you:
1. Activated your virtual environment (`.venv\Scripts\activate` on Windows or `source .venv/bin/activate` on macOS/Linux)
2. Installed in editable mode: `uv pip install -e .` or `pip install -e .`

### "pm: command not found"
Ensure your virtual environment is activated. The `pm` command is only available within the virtual environment.

### Gantt chart not generating
Check that you have tasks with valid dates in `01_plan.json`

### Editor not opening
Set your preferred editor:
- Windows: `set EDITOR=notepad`
- macOS/Linux: `export EDITOR=vim` or `export EDITOR=nano`

### Why use UV?
UV is significantly faster than pip (10-100x) and provides:
- Faster dependency resolution
- Better dependency locking
- Improved virtual environment management
- Compatible with pip workflows

However, regular pip works perfectly fine if you prefer it!

## License

MIT
