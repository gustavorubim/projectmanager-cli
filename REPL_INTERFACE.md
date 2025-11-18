# PM Agent - New REPL Interface

## What Changed?

The PM Agent now features a **unified REPL (Read-Eval-Print Loop) interface** where you enter once and use both natural language chat and slash commands seamlessly.

## New Workflow

### Before (Old Way)
```bash
pm chat                    # Enter chat mode
# (in chat) talk to agent
# Ctrl+C to exit
pm status                 # Run separate command
pm gantt --open           # Run another command
pm chat                   # Back to chat again
```

### After (New Way)
```bash
pm                        # Enter once
> How many tasks do we have?        # Natural chat
> /status                           # Slash command
> Create 3 new tasks               # Natural chat
> /gantt --open                     # Slash command
> /exit                             # Done
```

## How It Works

### 1. Enter the REPL
```bash
cd your-project
pm
```

First, you'll see a brief startup animation:
```
        [##########.....]
        Starting up...

        ___  __  __      _   ___ ___ _  _ _____
        |__) |  \/  |    /_\ / __| __| \| |_   _|
        |    |  |  |    / _ \ (_ | _|| .` | | |

        Ready!
```

Then the main interface appears:
```
+------------------------------------------------------------+
| PM Agent - AI-Powered Project Manager                      |
|                                                            |
| Type naturally to chat with the AI, or use slash commands. |
| Type /help for commands or /exit to quit.                  |
+------------------------------------------------------------+

Current project: your-project

>
```

**Note:** The animation can be disabled by setting `PM_NO_ANIMATION=1` in your environment.

### 2. Use Natural Language (No Slash)
Just type normally to chat with the AI:
```
> Add 5 tasks for the next sprint starting Monday
> Mark task #3 as completed
> What's the status of tasks assigned to Alice?
> Create a task for API testing due next Friday
```

The agent understands context and can:
- Create and update tasks
- Generate Gantt charts automatically
- Log decisions
- Answer questions about the project

### 3. Use Slash Commands (Quick Actions)

Type `/` followed by the command:

**View & Status:**
- `/status` - Show task table
- `/status --week` - AI weekly summary
- `/plan` - View plan JSON
- `/plan --edit` - Edit in your editor

**Visualization:**
- `/gantt` - Generate chart
- `/gantt --open` - Generate and open

**Quick Actions:**
- `/followup "Fix bug" --due 2025-12-01 --assign john` - Add task
- `/new project-name` - Create new project
- `/archive` - Archive current project

**Help & Exit:**
- `/help` - Show all commands
- `/exit` or `/quit` - Exit REPL

### 4. Mix Both!
You can freely mix natural language and slash commands:
```
> Create a mobile app development plan
Agent: [Creates initial tasks...]

> /status
[Shows table of tasks]

> Assign task #1 to Maria
Agent: [Updates task]

> /gantt --open
[Opens Gantt chart]

> What tasks are due this week?
Agent: [Lists tasks...]
```

## Benefits

### 1. **Seamless Workflow**
- No switching between `pm chat` and `pm status`
- Everything in one session
- Conversation history preserved throughout

### 2. **Best of Both Worlds**
- **Natural language** for complex requests
- **Slash commands** for quick actions
- Use what feels natural in the moment

### 3. **Faster**
- One startup time (agent loads once)
- No repeated commands
- Instant context switching

### 4. **Better UX**
- Clear visual boundaries with panels
- Consistent prompt (`>`)
- Helpful welcome message

## Examples

### Example 1: Quick Status Check
```
pm
> /status
> /exit
```

### Example 2: Planning Session
```
pm
> Let's plan the Q1 roadmap. We have 3 engineers and 12 weeks.
Agent: [Helps create comprehensive plan]
> /gantt --open
> Looks good! Now create follow-up tasks for each milestone.
Agent: [Creates follow-up tasks]
> /status
> /exit
```

### Example 3: Mixed Mode
```
pm
> /status
> Mark all tasks assigned to John as in_progress
> /followup "Code review session" --due 2025-12-01
> /gantt --open
> /exit
```

## Legacy Support

**All old commands still work!** For quick one-off actions:
```bash
pm status              # Quick status
pm gantt --open        # Quick chart view
pm new my-project      # Create project
```

But we recommend using the REPL for interactive sessions.

## Migration Guide

If you have scripts or workflows using the old commands:

**Option 1: Keep using direct commands**
```bash
pm status
pm gantt --open
# All still work!
```

**Option 2: Pipe commands to REPL**
```bash
echo -e "/status\n/gantt --open\n/exit" | pm
```

**Option 3: Use the REPL interactively**
```bash
pm  # Then use /status, /gantt, etc.
```

## Tips

1. **Use `/help` often** - It shows all available commands

2. **Natural language is powerful** - Don't hesitate to ask complex questions:
   - "What tasks are blocked and why?"
   - "Create a risk mitigation plan"
   - "Summarize progress this month"

3. **Slash commands are fast** - For known actions:
   - `/status` instead of "show me the tasks"
   - `/gantt --open` instead of "generate and open the gantt chart"

4. **Mix them freely** - There's no "mode switching"
   ```
   > Show me all tasks
   > /status
   > What's next?
   > /exit
   ```

5. **Press Ctrl+C** to exit at any time

6. **Tab completion** works on slash commands (if your terminal supports it)

## Keyboard Shortcuts

- **Ctrl+C** - Exit immediately
- **Ctrl+D** - Exit (EOF)
- **Up/Down arrows** - Command history (terminal dependent)
- **Ctrl+L** - Clear screen (terminal dependent)

## What's Next?

Try it now:
```bash
cd mobile-app-launch
pm
> /help
```

Enjoy the new seamless experience! 🚀
