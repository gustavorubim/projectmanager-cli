# PM Agent - Startup Animation ✨

## Overview

The PM Agent now features a delightful startup animation that plays when you enter the interactive REPL, similar to modern CLI tools like GitHub Copilot CLI and Claude Code.

## What You'll See

When you run `pm`, you'll see a brief animated sequence:

### Frame 1: Initializing
```
        [...............]
        Initializing...
```

### Frame 2: Loading
```
        [###............]
        Loading agent...
```

### Frame 3: Starting
```
        [##########.....]
        Starting up...
```

### Frame 4: Ready!
```
        ___  __  __      _   ___ ___ _  _ _____
        |__) |  \/  |    /_\ / __| __| \| |_   _|
        |    |  |  |    / _ \ (_ | _|| .` | | |

        Ready!
```

Then smoothly transitions to the main interface.

## Features

### 🎨 **Smooth Animation**
- Uses Rich's Live display for flicker-free updates
- Progressively builds the logo
- Shows loading status at each stage

### ⚡ **Fast & Non-Intrusive**
- Total duration: ~1 second
- Transient mode: disappears after playing
- Doesn't slow down startup

### 🎛️ **Configurable**
- Can be disabled with `PM_NO_ANIMATION=1`
- Respects user preferences
- Perfect for scripts and automation

## Technical Details

### Implementation
- Built with Rich's `Live` display
- Uses transient rendering (animation disappears after completion)
- 4 frames at 20 FPS
- Total animation time: 0.8 seconds

### ASCII Art
- Uses simple ASCII characters for maximum compatibility
- No Unicode block characters (Windows-safe)
- Works on all terminals and platforms

### Color Scheme
- Cyan color palette (matches PM Agent branding)
- Progressive brightness increase
- Final frame is bold bright cyan for emphasis

## Disabling the Animation

If you prefer instant startup or are using PM Agent in scripts:

### Option 1: Environment Variable
```bash
export PM_NO_ANIMATION=1  # Unix
set PM_NO_ANIMATION=1     # Windows
pm
```

### Option 2: In .env File
```bash
# Add to .env
PM_NO_ANIMATION=1
```

### Option 3: One-Time Disable
```bash
PM_NO_ANIMATION=1 pm
```

## Why Add an Animation?

### 1. **Better User Experience**
Modern CLI tools feel polished and professional. The animation adds personality and indicates that the system is loading.

### 2. **Visual Feedback**
Shows the user that PM Agent is starting up and loading the AI model, rather than appearing to freeze.

### 3. **Brand Identity**
The animated logo reinforces the PM Agent brand and makes the tool more memorable.

### 4. **Professional Polish**
Following patterns from popular CLI tools like:
- GitHub Copilot CLI
- Claude Code
- Warp Terminal
- Fig

## Performance Impact

**None!** The animation:
- Runs while the agent initializes anyway
- Uses transient rendering (no persistent overhead)
- Can be completely disabled if needed
- Adds < 1 second to perceived startup time

## Comparison to Other Tools

### GitHub Copilot CLI
- Shows animated thinking spinner
- Displays status messages
- PM Agent: Similar progress bar + logo reveal

### Claude Code
- Shows startup banner
- Animated text effects
- PM Agent: Similar ASCII art animation

### Warp Terminal
- Smooth fade-in effects
- Progressive UI building
- PM Agent: Similar progressive reveal

## Future Enhancements

Potential improvements for future versions:

1. **Theme Support**
   - Light/dark mode animations
   - Custom color schemes

2. **More Animations**
   - Different animations for different contexts
   - Seasonal themes (optional)

3. **Sound Effects** (optional)
   - Subtle startup sound
   - Completion chime

4. **Custom Animations**
   - User-definable ASCII art
   - Plugin system for animations

## Examples

### Normal Startup
```bash
cd my-project
pm
# [Animation plays]
# Main interface appears
>
```

### Quick Startup (No Animation)
```bash
cd my-project
PM_NO_ANIMATION=1 pm
# Main interface appears immediately
>
```

### In Scripts
```bash
#!/bin/bash
export PM_NO_ANIMATION=1
echo "/status" | pm
# No animation, just output
```

## Accessibility

The animation is designed to be accessible:

- **No flashing**: Smooth transitions, no rapid flashing
- **Can be disabled**: Full functionality without animation
- **Color safe**: Works in monochrome terminals
- **Screen reader friendly**: Transient (doesn't pollute output)

## Technical Notes

### Animation Timing
- Frame 1: 0.2s (Initializing)
- Frame 2: 0.2s (Loading agent)
- Frame 3: 0.2s (Starting up)
- Frame 4: 0.2s (Ready! + logo)
- Hold: 0.2s (Final frame display)
- **Total: 1.0s**

### ASCII Art Design
```
___  __  __      _   ___ ___ _  _ _____
|__) |  \/  |    /_\ / __| __| \| |_   _|
|    |  |  |    / _ \ (_ | _|| .` | | |
```

This represents "PM AGENT" in a stylized ASCII format that:
- Works on all terminals
- Scales well
- Is readable at a glance
- Matches the branding

## Conclusion

The startup animation adds a touch of delight and professionalism to PM Agent without any performance penalty. It can be easily disabled for users who prefer instant startup or are using PM Agent in automated scripts.

Try it now:
```bash
cd your-project
pm
```

Enjoy the show! 🎬✨
