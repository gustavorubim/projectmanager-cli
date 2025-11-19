# CLI Project Manager Agent Master Plan

Here is the comprehensive, end-to-end plan to build the CLI Project Manager Agent. It covers the directory structure, database schema, code architecture, and deployment strategy.



## Phase 1: Project Scaffolding & Setup
**Goal:** Set up the Python environment and the tool's internal structure.

### 1.1 Dependencies
- use UV
- Create a `pyproject.toml` or `requirements.txt`.
- Core: `python>=3.11`
- AI Logic: `langchain-openai`, `langgraph`, `langchain-core`
- CLI UI: `typer[all]`, `rich`
- Data/Viz: `pandas`, `matplotlib`
- Utilities: `python-dotenv`, `pydantic`

### 1.2 Source Code Structure
This is the structure of the tool itself (not the projects it manages).

```
pm-agent/
+-- pyproject.toml
+-- .env                    # API keys
+-- src/
    +-- __init__.py
    +-- cli.py              # Entry point (Typer)
    +-- agent.py            # LangGraph definition
    +-- tools.py            # File I/O & logic
    +-- schema.py           # Data models (Pydantic/JSON)
    +-- viz.py              # Gantt/Burndown generation logic
```

## Phase 2: The Data Layer (Filesystem Database)
**Goal:** Define how the agent stores data on disk so it is readable by both humans and the LLM.

### 2.1 The Project "World"
Every project created by the tool will have this standardized structure:

- `00_charter.md`: Natural language goals and scope.
- `01_plan.json`: Structured source of truth for tasks.
- `02_logs.md`: Chronological journal of notes and decisions.
- `.pm/`: Hidden folder for the agent's brain (SQLite history).


~/projects/
├── apollo-launch/
│   ├── 00_project_charter.md          # Goal, scope, success criteria, stakeholders
│   ├── 01_plan.json                   # Full task list with dates, assignees, status, dependencies
│   ├── 02_followups.json              # All open & closed action items
│   ├── 03_risks_issues.md
│   ├── 04_decisions.log
│   ├── 05_meeting_notes/
│   ├── documentation.md               # Single living doc the agent edits
│   ├── gantt_latest.png               # Auto-updated every time plan changes
│   ├── gantt_latest.svg               # Optional vector version
│   └── attachments/                   # PDFs, images, exports, whatever
│
├── website-redesign/
│   same structure...
│
└── _templates/
    └── new_project_template/
        ├── 00_project_charter.md
        └── 01_plan.json (empty)

### 2.2 JSON Schema (`01_plan.json`)
The agent must strictly adhere to this schema to prevent visualization errors.

```json
[
  {
    "id": 1,
    "title": "Design Database",
    "assignee": "Alice",
    "start_date": "2025-10-01",
    "due_date": "2025-10-05",
    "status": "done",
    "dependencies": []
  }
]
```

## Phase 3: Tool Implementation (The "Hands")
**Goal:** Create safe, atomic functions the agent can call. Do not allow the agent to overwrite whole files arbitrarily.

### 3.1 Read Tools
- `read_context()`: Reads `00_charter.md`, `02_logs.md`, and a summary of `01_plan.json`.
- `read_plan()`: Returns the raw JSON of the plan.

### 3.2 Write Tools (Atomic)
- `add_task(title, assignee, due_date)`: Appends to the JSON array.
- `update_task(id, status, assignee)`: Finds ID, updates the field, and saves.
- `append_log(content)`: Appends a timestamped header and text to `02_logs.md`.
- `write_file(filename, content)`: (Restricted) Only allows creating new files in `docs/` or `assets/`, preventing overwriting the plan JSON directly.

### 3.3 Visualization Tools
- `generate_gantt()`:
  - Loads `01_plan.json` into Pandas.
  - Uses Matplotlib to draw a timeline.
  - Saves to `gantt.png`.
  - Trigger: Called automatically whenever `add_task` or `update_task` succeeds.

## Phase 4: The Agent Architecture (The "Brain")
**Goal:** Build the ReAct loop using LangGraph.

### 4.1 State Definition
```python
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
```

### 4.2 The Graph
- Node: `Agent` calls LLM (GPT-4o).
- Node: `Tools` executes Python functions from Phase 3.
- Edge: `ConditionalEdge` checks for `tool_calls`.
- Memory: `SqliteSaver` pointed at `./.pm/state.sqlite`.

### 4.3 System Prompt
```
You are a Project Manager operating on the local file system.
Your source of truth is 01_plan.json.
Never output a Gantt chart directly; call the generate_gantt tool.
When asked to plan, check available resources in the Charter first.
```

## Phase 5: The CLI Interface (The "Mouth")
**Goal:** Implement the `pm` commands using Typer.

### 5.1 Direct Commands (No LLM)
- `pm new <name>`: Creates folders and template files.
- `pm plan --edit`: Opens system editor (vim/nano) on `01_plan.json`. Triggers `generate_gantt` on save.
- `pm status` (default): Reads JSON and prints a Rich table of tasks.
- `pm gantt --open`: Opens the PNG file.

### 5.2 Agent Commands (LLM Involved)
- `pm chat`: Interactive loop. Pass `thread_id="main"`.
- `pm chat --thread backlog`: Separate context window for brainstorming.
- `pm status --week`: Injects a prompt, "Summarize logs and plan progress for this week" and returns a text summary.
- `pm followup`: Shortcut that constructs a prompt, "Add a task for [User Input] and log it," runs the agent once, then exits.

## Phase 6: Deployment & Workflow
**Goal:** Make it usable on your machine.

### 6.1 Installation
Create an alias in your shell profile:

```bash
alias pm="python3 /path/to/pm-agent/src/cli.py"
```

### 6.2 End-to-End User Workflow
- **Monday Morning:**
  - `pm new launch-campaign`
  - `pm chat` ? "Let's map out the launch. We need a landing page by Friday." (agent creates tasks)
- **Mid-Week:**
  - `pm status` ? See list of tasks.
  - `pm plan --edit` ? Mark "Draft Copy" as done manually.
  - `pm gantt --open` ? Check the visual timeline.
- **Friday:**
  - `pm status --week` ? Get a summary for your boss.
  - `pm archive` ? Move project to the archive folder.


  additinal chats:

  pm new apollo-launch                    # creates folder + charter + empty plan
    pm chat                                 # default project = current folder
    pm chat website-redesign                # switch project
    pm chat --thread backlog                # personal non-project thread
    pm plan                                 # show current plan + Gantt
    pm plan --edit                          # opens plan.json in $EDITOR, then auto-regenerates Gantt
    pm followup "Fix login bug" --due 2025-12-01 --assign john@company.com
    pm status                               # beautiful dashboard in terminal
    pm gantt --open                         # opens latest Gantt in default viewer
    pm archive                              # moves folder to ../_archive/apollo-launch_done_20251218

### 6.3 Security & Privacy Check
- Files: All stay in the folder.
- API: Only text (prompts) is sent to the Google Gemini API.
- Memory: Stored in `.pm/state.sqlite`. Deleting the `.pm` folder clears conversation history while keeping project files safe.

## Implementation Checklist
1. Create `src/tools.py` with `add_task` and `generate_gantt`.
2. Create `src/agent.py` setting up the LangGraph with `SqliteSaver`.
3. Create `src/cli.py` with Typer commands.
4. Test `pm new` to ensure folder creation works.
5. Test `pm chat` and verify `01_plan.json` updates when you tell the agent to add tasks.
6. Test `pm plan --edit` and verify the Gantt chart auto-regenerates.

