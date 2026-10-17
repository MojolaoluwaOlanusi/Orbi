# Orbi

> A little round desktop robot that keeps you company while you code.

Orbi is a small, frameless, always-on-top desktop companion written in Python. He rolls around the bottom of your screen, blinks, looks at your cursor, and reacts to what you're doing. When your tests pass, he celebrates. When your build takes too long, he gets nervous. When you've been sitting still for an hour, he reminds you to drink water.

**He is fully offline.** No AI, no cloud, no telemetry, no microphone. He just sits there and keeps you company.

![Orbi Demo](docs/preview.gif)

## Features

- **Alive:** Rolls on a real 3D sphere (his patches actually roll as he moves), blinks, breathes, and tracks your cursor.
- **Interactive:** Left-click to boop, double-click for a rubber-duck debugging chat, drag to pick him up (he falls and bounces).
- **Developer-aware:** Reacts when your tests pass/fail, gets nervous on slow builds, and watches your git repos.
- **Healthy:** Reminds you to drink water, stretch, and follow the 20-20-20 rule for your eyes.
- **Focus:** Built-in Pomodoro timer with a visual focus ring and daily counts.
- **Customizable:** 10 themes (Hacker, Ninja, Sakura, etc.), 4 seasonal outfits, and 3 sizes.
- **Progress:** 24 unlockable achievements and local usage statistics.
- **Invisible Infrastructure:** Lives purely in the system tray. No taskbar entry, no console window, no start menu clutter.
- **Zero Dependencies:** Drawn entirely in code. No image assets, no external libraries needed by the user.

## Installation

You do not need Python installed to run Orbi.

1. Go to the [Releases](https://github.com/YOUR_USERNAME/orbi/releases) page.
2. Download the latest `Orbi-vX.X.X-windows.zip` file.
3. Unzip the folder anywhere on your computer.
4. Run `Orbi.exe`.

**To make him start automatically:**
Right-click the little robot -> **Settings** -> **System** tab -> check **"Start with Windows"**. From then on, he boots silently and lives in your system tray.

## How to use Orbi

- **Left-click** Orbi to boop him.
- **Right-click** Orbi for settings.
- **Double-click** Orbi to open the rubber-duck debugging chat.
- **Drag** him to move him.
- **Click the tray icon** to put him to sleep or wake him up.
- Press **`Ctrl+Alt+O`** from anywhere to wake/sleep him instantly.

## CLI Integration

You can talk to Orbi from your terminal, shell scripts, or CI/CD pipelines. (Orbi must be running).

```bash
# React to test results
npm test && orbi.exe pass || orbi.exe fail

# Track build times
orbi.exe build-start
npm run build
orbi.exe build-done

# Make him say something
orbi.exe say "Deployment complete!"

# Do not disturb for 45 minutes
orbi.exe dnd 45