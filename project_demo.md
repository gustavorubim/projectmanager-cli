# PM Agent - Complete Feature Walkthrough

This guide demonstrates all features of the PM Agent CLI tool through a realistic project example.

## Prerequisites

Before starting, ensure you have:

1. **Installed UV** (recommended):
   ```bash
   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Windows
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. **Set up the environment**:
   ```bash
   # Create and activate virtual environment
   uv venv

   # Activate:
   # Windows: .venv\Scripts\activate
   # macOS/Linux: source .venv/bin/activate

   # Install the package
   uv pip install -e .
   ```

3. **Configure Gemini API access**:
```bash
cp .env.example .env  # or 'copy' on Windows
# Edit .env and set:
# GOOGLE_API_KEY=your-gemini-api-key
# Optional: GEMINI_MODEL=gemini-2.5-flash
```

> **Note:** If you prefer pip over UV, that's fine too! Just use `pip install -e .` instead of `uv pip install -e .`

---

## Walkthrough: Building a Mobile App

Let's manage a mobile app development project from start to finish.

### Step 1: Create a New Project

```bash
pm new mobile-app-launch
```

**Output:**
```
OK Project 'mobile-app-launch' created successfully!

Next steps:
  cd mobile-app-launch
  pm chat
```

**What happened:**
- Created project directory `mobile-app-launch/`
- Generated standard project files:
  - `00_project_charter.md` - Project goals and scope
  - `01_plan.json` - Task database (empty)
  - `02_followups.json` - Action items
  - `03_risks_issues.md` - Risk tracking
  - `04_decisions.log` - Decision journal
  - `.pm/` - Agent conversation memory

---

### Step 2: Define Your Project Charter

```bash
cd mobile-app-launch
```

Edit the charter to set context for the agent:

```bash
# On Windows
notepad 00_project_charter.md

# On Mac/Linux
nano 00_project_charter.md
```

**Example charter content:**
```markdown
# Mobile App Launch Project

## Goal
Launch a productivity mobile app for iOS and Android by Q1 2026.

## Scope
**Included:**
- User authentication (email/Google/Apple)
- Task management features
- Cloud sync
- Basic analytics
- App Store submission

**Not Included:**
- Premium subscription features (Phase 2)
- Web version
- Enterprise features

## Success Criteria
- 10,000 downloads in first month
- 4+ star rating on both stores
- < 2% crash rate
- Launch on time and within budget

## Stakeholders
- **Product Owner:** Sarah Chen (sarah@company.com)
- **Tech Lead:** Alex Kumar (alex@company.com)
- **Designer:** Maria Lopez (maria@company.com)
- **QA Lead:** Jordan Taylor (jordan@company.com)
```

Save and close the file.

---

### Step 3: Interactive Planning with the Agent

Start a chat session:

```bash
pm chat
```

**Conversation Example:**

```
You: Let's create a plan for this mobile app launch. We need to break it down into phases.

Agent: I'll help you create a structured plan. Let me first read the project charter to understand the scope.

[Agent calls read_context() and read_plan()]

Based on your charter, I can see you're launching a productivity app for iOS and Android by Q1 2026. Let me propose a phased approach:

Phase 1: Foundation (Weeks 1-4)
- Design system and wireframes
- Backend API setup
- Authentication implementation

Phase 2: Core Features (Weeks 5-8)
- Task management UI
- Cloud sync implementation
- Data persistence

Phase 3: Polish & Testing (Weeks 9-10)
- Analytics integration
- QA testing
- Bug fixes

Phase 4: Launch (Weeks 11-12)
- App Store submission
- Marketing preparation
- Production deployment

Would you like me to create detailed tasks for each phase?

You: Yes, start with Phase 1. Add specific tasks with assignees and dates. We're starting today.

Agent: I'll create the Phase 1 tasks now.

[Agent calls add_task() multiple times]

Added 6 tasks for Phase 1:
1. Design system and component library - Maria Lopez (Week 1)
2. Backend API architecture - Alex Kumar (Week 1)
3. Set up development environments - Alex Kumar (Week 1)
4. Implement email authentication - Alex Kumar (Week 2)
5. Implement OAuth (Google/Apple) - Alex Kumar (Week 2)
6. Create onboarding screens - Maria Lopez (Week 2)

The plan has been updated and a Gantt chart has been generated.

You: Great! Log this decision and let's add a note about using React Native as our framework.

Agent: I'll log that decision now.

[Agent calls append_log()]

Decision logged. I've recorded that we're using React Native as the mobile framework for cross-platform development. This is important context for future planning.
```

---

### Step 4: View Project Status

Exit the chat (type `exit`) and check status:

```bash
pm status
```

**Output:**
```
┏━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ ID ┃ Status      ┃ Title                              ┃ Assignee     ┃ Due Date   ┃
┡━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ 1  │ [ ] todo    │ Design system and component lib   │ Maria Lopez  │ 2025-11-25 │
│ 2  │ [ ] todo    │ Backend API architecture           │ Alex Kumar   │ 2025-11-25 │
│ 3  │ [ ] todo    │ Set up development environments    │ Alex Kumar   │ 2025-11-25 │
│ 4  │ [ ] todo    │ Implement email authentication     │ Alex Kumar   │ 2025-12-02 │
│ 5  │ [ ] todo    │ Implement OAuth (Google/Apple)     │ Alex Kumar   │ 2025-12-02 │
│ 6  │ [ ] todo    │ Create onboarding screens          │ Maria Lopez  │ 2025-12-02 │
┗━━━━┷━━━━━━━━━━━━━┷━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┷━━━━━━━━━━━━━━┷━━━━━━━━━━━━┛

Total tasks: 6
```

**Status indicators:**
- `[ ]` - todo
- `[>]` - in_progress
- `[X]` - done
- `[!]` - blocked

---

### Step 5: View the Gantt Chart

Generate and view the project timeline:

```bash
pm gantt --open
```

**What happens:**
1. Reads `01_plan.json`
2. Generates a timeline visualization with Matplotlib
3. Saves to `gantt_latest.png`
4. Opens the image in your default viewer

The chart shows:
- All tasks as horizontal bars
- Task duration (start to due date)
- Assignee names on each bar
- Color-coded by status
- Chronological timeline on X-axis

---

### Step 6: Quick Task Addition with `followup`

Add an urgent task without entering chat mode:

```bash
pm followup "Fix login bug reported by beta testers" --due 2025-11-20 --assign "Jordan Taylor"
```

**Output:**
```
Adding follow-up task...

OK Task #7 'Fix login bug reported by beta testers' added successfully.
```

This command:
- Creates a new task
- Assigns it to Jordan
- Sets the due date
- Logs the decision
- Updates the Gantt chart automatically

---

### Step 7: Manual Plan Editing

For bulk changes, edit the plan directly:

```bash
pm plan --edit
```

This opens `01_plan.json` in your default editor (`$EDITOR` or notepad on Windows).

**Example edit** - Mark task #1 as done:

```json
[
  {
    "id": 1,
    "title": "Design system and component library",
    "assignee": "Maria Lopez",
    "start_date": "2025-11-18",
    "due_date": "2025-11-25",
    "status": "done",
    "dependencies": []
  },
  ...
]
```

Save and close. The Gantt chart auto-regenerates!

**Output:**
```
Opening plan in notepad...
Regenerating Gantt chart...
OK Gantt chart updated!
```

---

### Step 8: Using Separate Conversation Threads

Use different threads for different contexts:

**Main project thread:**
```bash
pm chat
```

**Brainstorming thread (no memory of main project):**
```bash
pm chat --thread brainstorming
```

```
You: What features should we prioritize for MVP?

Agent: For an MVP of a productivity app, I'd recommend...
```

**Team standup thread:**
```bash
pm chat --thread standup
```

```
You: Summarize what's been completed this week and what's blocked.

Agent: [Analyzes plan and provides summary]
```

Each thread has independent conversation memory but accesses the same project files.

---

### Step 9: Weekly Status Report

Generate an AI summary for stakeholder updates:

```bash
pm status --week
```

**Output:**
```
Generating weekly summary...

# Weekly Project Summary

## Progress This Week
We've made solid progress on Phase 1 foundation work:

**Completed:**
- Design system and component library (Maria)
- Backend API architecture (Alex)
- Development environment setup (Alex)

**In Progress:**
- Email authentication implementation (Alex) - 80% complete
- OAuth integration (Alex) - blocked pending API keys

**Blocked:**
- OAuth implementation needs production API keys from Google/Apple

## Key Decisions
- Chose React Native as our cross-platform framework
- Using Firebase for authentication backend
- Implementing offline-first architecture for task sync

## Next Week's Focus
- Complete authentication layer
- Begin UI implementation for task management
- Maria to start onboarding screen designs

## Risks
- OAuth setup delay could push Phase 1 by 2-3 days
- Need to secure beta tester group for QA phase
```

Perfect for copying into status reports or stand-ups!

---

### Step 10: Creating Project Documentation

The agent can create documentation files:

```bash
pm chat
```

```
You: Create a technical architecture document in docs/ explaining our backend setup.

Agent: I'll create that documentation for you.

[Agent calls write_file("docs/architecture.md", content)]

Created docs/architecture.md with the technical architecture overview. The document covers:
- System architecture diagram description
- Backend tech stack (Node.js, Express, PostgreSQL)
- Authentication flow
- API structure
- Database schema
- Deployment strategy

You can review and edit it as needed.
```

**Allowed documentation paths:**
- `docs/` - General documentation
- `documentation.md` - Main living doc
- `05_meeting_notes/` - Meeting minutes
- `attachments/` - PDFs, exports, etc.

---

### Step 11: Viewing the Decision Log

Check all logged decisions:

```bash
# On Windows
type 04_decisions.log

# On Mac/Linux
cat 04_decisions.log
```

**Example output:**
```
# Decision Log

## 2025-11-18 14:23:45
Created initial project plan with 4 phases spanning 12 weeks. Focus is on launching both iOS and Android versions simultaneously.

## 2025-11-18 14:28:12
Decided to use React Native as the mobile framework for cross-platform development. This enables code sharing between iOS and Android while maintaining native performance.

## 2025-11-18 14:35:07
Added urgent task to fix login bug reported by beta testers. Assigned to Jordan Taylor with priority due date.

## 2025-11-18 15:10:22
Chose Firebase Authentication for backend auth due to built-in OAuth providers and easy React Native integration.
```

---

### Step 12: Managing Multiple Projects

Switch between projects easily:

```bash
# Create another project
pm new website-redesign

# Chat about the new project
pm chat website-redesign
```

Or navigate to the project directory:

```bash
cd ../website-redesign
pm status
```

---

### Step 13: View Raw Plan Data

Inspect the JSON structure:

```bash
pm plan
```

**Output:**
```json
[
  {
    "id": 1,
    "title": "Design system and component library",
    "assignee": "Maria Lopez",
    "start_date": "2025-11-18",
    "due_date": "2025-11-25",
    "status": "done",
    "dependencies": []
  },
  {
    "id": 2,
    "title": "Backend API architecture",
    "assignee": "Alex Kumar",
    "start_date": "2025-11-18",
    "due_date": "2025-11-25",
    "status": "in_progress",
    "dependencies": [1]
  }
]
```

---

### Step 14: Advanced Agent Interactions

**Adding dependencies between tasks:**

```bash
pm chat
```

```
You: Task 4 (email auth) depends on task 2 (backend API). Update the dependencies.

Agent: I'll update task 4 to depend on task 2.

[Agent calls update_task(4, dependencies=[2])]

Task #4 updated successfully. Email authentication now depends on Backend API architecture being complete.
```

**Bulk status updates:**

```
You: Mark all tasks assigned to Maria as in_progress.

Agent: I'll update Maria's tasks now.

[Agent calls update_task() for each of Maria's tasks]

Updated 2 tasks to in_progress:
- Task #1: Design system and component library
- Task #6: Create onboarding screens
```

**Risk management:**

```
You: We might not get OAuth API keys in time. Log this as a risk.

Agent: I'll document this risk.

[Agent calls write_file("03_risks_issues.md", updated_content)]

Added risk to the risks & issues log:
- OAuth API key delays could block authentication implementation
- Mitigation: Start email auth first, add OAuth later if needed
- Impact: Medium (could delay Phase 1 by 3-5 days)
```

---

### Step 15: Archiving Completed Projects

When the project is done:

```bash
pm archive
```

**Output:**
```
Archiving project to ../archive/mobile-app-launch_done_20260315...
OK Project archived successfully!
```

The project is moved to `_archive/mobile-app-launch_done_20260315/` preserving all files and history.

---

## Advanced Tips

### 1. Custom Queries

The agent understands natural language:

```
You: Show me all tasks due this week.
You: What's blocking Alex right now?
You: Add a task for API documentation, due in 2 days.
You: Which tasks are behind schedule?
You: Create a testing phase with 5 QA tasks starting next Monday.
```

### 2. Team Workflows

**Daily standup:**
```bash
pm status --week
```

**Sprint planning:**
```bash
pm chat
> "Plan the next 2-week sprint with tasks from the backlog"
```

**Retrospective:**
```bash
pm chat --thread retro
> "Review completed tasks and identify what went well and what didn't"
```

### 3. Integration Ideas

**Git hooks:**
Create a git commit hook to auto-update status:
```bash
#!/bin/bash
# .git/hooks/pre-commit
pm followup "Review and merge PR #123" --due $(date -d "+2 days" +%Y-%m-%d)
```

**CI/CD:**
Update task status automatically from deployment scripts:
```bash
# deploy.sh
pm chat <<< "Mark task #15 (Deploy to production) as done"
```

### 4. File Structure Reference

```
mobile-app-launch/
├── 00_project_charter.md       # Edit: Project scope and goals
├── 01_plan.json                # Edit: Task database (auto-updated by agent)
├── 02_followups.json           # Edit: Action items tracker
├── 03_risks_issues.md          # Edit: Risk log
├── 04_decisions.log            # View: Timestamped decision journal
├── 05_meeting_notes/           # Create: Meeting minutes
│   ├── 2025-11-18_kickoff.md
│   └── 2025-11-25_standup.md
├── docs/                       # Create: Technical documentation
│   ├── architecture.md
│   └── api-spec.md
├── documentation.md            # Create: Main living documentation
├── gantt_latest.png            # Auto: Latest timeline visualization
├── attachments/                # Add: PDFs, exports, images
└── .pm/                        # Don't touch: Agent memory
    └── state.sqlite
```

---

## Troubleshooting

**Problem:** Agent isn't responding

**Solution:** Verify `.env` defines `GOOGLE_API_KEY` (Gemini key from AI Studio) and that it's valid

---

**Problem:** Gantt chart looks cluttered

**Solution:** Break large projects into sub-projects:
```bash
pm new mobile-app-phase1
pm new mobile-app-phase2
```

---

**Problem:** Want to clear conversation history

**Solution:** Delete the memory while keeping project files:
```bash
rm -rf .pm/
```

Next `pm chat` starts fresh!

---

## Command Quick Reference

```bash
# Project Management
pm new <project>              # Create new project
pm archive                    # Archive current project

# Status & Planning
pm status                     # View task table
pm status --week              # AI weekly summary
pm plan                       # Show plan JSON
pm plan --edit                # Edit plan in editor

# Agent Interaction
pm chat                       # Interactive chat (main thread)
pm chat <project>             # Chat about specific project
pm chat --thread <name>       # Use separate thread
pm followup "task" --due DATE --assign NAME  # Quick task add

# Visualization
pm gantt                      # Generate chart
pm gantt --open               # Generate and open chart
```

---

## Next Steps

Now that you've seen all the features, try:

1. **Create a real project** for something you're working on
2. **Customize the charter** with your actual goals and stakeholders
3. **Use the agent daily** to track progress and make decisions
4. **Share the Gantt chart** in team meetings
5. **Experiment with threads** for different conversation contexts

Happy project managing! 🚀
