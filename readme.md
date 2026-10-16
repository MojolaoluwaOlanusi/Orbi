# Orbi — your little desktop robot

> A tiny, round companion that sits at the bottom of your screen, keeps
> you company while you work, reminds you to take care of yourself, and
> quietly celebrates when your tests pass.

---

## What is Orbi?

Orbi is a small robot that lives on your desktop. He rolls around, blinks,
looks at your cursor, and occasionally says something useful. He has a
spherical body and a floating head, like a certain droid you may have
seen, but he's entirely his own character — and he's rendered in code, so
he's crisp at any size and never looks out of place.

Orbi is **not** an AI assistant and he never connects to the internet. He
can't look at your code. He can't read your files. He just sits there,
reacts to what you're doing, and makes the desk a slightly better place
to be.

He's here to do four things:

- **Keep you company** — a small presence that reacts to what you're doing.
- **Keep you healthy** — reminders to drink water, stretch, and rest your eyes.
- **Keep you focused** — a built-in focus timer that stays out of the way.
- **Keep you honest** — a rubber-duck debugging buddy for when you're stuck.

---

## At a glance

- Sits in the system tray. No taskbar clutter. No console window.
- Rolls, hops, blinks, breathes, and looks at your cursor.
- Right-click for settings — themes, sizes, reminders, everything.
- Left-click to boop him. Double-click for rubber-duck debugging.
- Drag him anywhere. Release and he falls and bounces back into place.
- Reacts when your tests pass or fail. Gets nervous when builds run long.
- Watches your git repos and celebrates your commits.
- Ten themes, four seasonal outfits, achievements, and stats.
- Fully offline. Nothing leaves your computer.

---

## Meet your companion

| You do this… | Orbi does this |
|---|---|
| **Left-click** Orbi | He gets happy. Little hearts float up. |
| **Double-click** Orbi | Opens the rubber-duck debugging chat. |
| **Right-click** Orbi | Opens the settings window. |
| **Drag** Orbi | You pick him up. Let go and he falls, lands, and bounces. |
| **Click** the tray icon | Puts him to sleep (or wakes him up). |
| **Right-click** the tray icon | Opens a quick menu: sleep, DND, dance, stats, achievements, quit. |

That's the whole interaction model. There's nothing to memorise.

---

## Feature guide

Everything below is optional. Orbi works out of the box with sensible
defaults. Open the settings window (right-click Orbi) to change anything.

### Presence and personality

**Rolling body.** Orbi has a proper 3D sphere for a body. When he moves,
his patches and seams roll across the surface — they don't just slide.
The same effect drives his little bounce after you drop him.

**Blinking and breathing.** He blinks at random intervals and gently
breathes when idle. Small touches that make him feel alive rather than
animated.

**Eye tracking.** His eyes follow your cursor. Look away from the screen
and he looks around on his own. Turn this off if it distracts you.

**Moods.** Orbi has seven moods and cycles between them naturally —
`normal`, `happy`, `sad`, `sleepy`, `panic`, `alert`, and `listening`.
You'll see the difference in his eye color, head tilt, and antenna sway.

**Reactions.** When something notable happens, he pops a small "!" or "?"
above his head. A quick punctuation mark, then it fades.

**Speech bubbles.** When he has something to say, a bubble appears above
his head. No popups, no notification tray spam — just the robot talking.

### Focus timer

Start a focus block from the tray menu (right-click the tray icon →
"Start focus timer"). A faint ring appears around Orbi to remind you
you're in a session, and he stops commenting so he doesn't break your
concentration.

You can configure:

- How long a focus block runs (default: 25 minutes).
- How long short breaks run (default: 5 minutes).
- How long long breaks run (default: 15 minutes).
- How many focus blocks before a long break (default: 4).

Orbi tracks how many sessions you complete each day. The count appears
right in the tray menu.

### Health reminders

Orbi can nudge you — gently, and only when you're actually at your desk —
to do the things your body needs:

- **Drink water** — every 60 minutes.
- **Stretch** — every 45 minutes.
- **Rest your eyes** — every 20 minutes (the 20-20-20 rule).
- **Stand up** — every 90 minutes.
- **Posture check** — every 30 minutes.

Each one can be turned on or off individually. If you're away from the
keyboard, Orbi notices and skips the reminder rather than showing a
backlog.

### Do Not Disturb

Right-click the tray icon → "Do-not-disturb 30 min" silences Orbi. He
stops commenting, stops reminding, and stops responding to notifications.
He'll still respond to clicks — you can boop him whenever you want.

You can also set a custom duration from the settings window.

### Developer awareness

**Test runs.** Pipe your test command into Orbi and he reacts. When tests
pass, he celebrates with confetti. When they fail, he goes sad. There's a
one-line command you can drop into any shell script.

**Build tracking.** Tell Orbi a build started and he watches the clock.
If the build takes longer than 8 seconds, he starts getting nervous. Past
five minutes and he's outright pacing. When the build finishes, he's
either relieved or disappointed, depending on the outcome.

**Git awareness.** Add one or more git repositories and Orbi will:

- Celebrate every commit you make with a small confetti burst.
- Politely nudge you if a repository has uncommitted changes for more
  than 90 minutes.
- Show the last commit message in his celebration.

He never pushes, pulls, or modifies anything. He just watches.

### System awareness

If your machine supports it, Orbi will:

- Warn you when your battery drops below 20% and you're unplugged.
- Warn you if your CPU stays pinned above 92% for a sustained period.

Both are off-by-default-safe and only trigger if you leave them enabled.

### Rubber-duck debugging

Double-click Orbi to open a chat window. Explain what you're trying to
do, and what's going wrong instead. Orbi responds with the questions
you'd ask yourself if you were looking at the problem fresh:

- *What did you expect to happen?*
- *What changed since it last worked?*
- *What does the error message actually say?*
- *Can you make the problem smaller?*

He also listens for keywords. If you mention a `null` value, a race
condition, a regex, or a timezone, he asks the questions that keyword
tends to attract.

When you figure it out — and you will — he'll notice. Say something like
"oh, it was a typo" and he'll celebrate the win with you. This is the
classic "rubber duck debugging" technique, just with a robot instead of
a duck. It works. Really.

### Achievements

Orbi tracks 24 achievements. They unlock silently in the background, and
each one triggers a small celebration.

- **First Steps** — Orbi rolled off the production line.
- **Green Day** — first passing test run.
- **Deep Focus** — 25 pomodoros completed.
- **Shipping Machine** — 500 commits celebrated.
- **Rubber Duck's Best Friend** — 25 duck sessions.
- **Night Owl** — used Orbi past midnight.
- **Personal Space** — booped Orbi 100 times.
- **Week of Focus** — used Orbi 7 days in a row.
- And 16 more to discover.

Open the achievements window from the tray menu or the About tab in
settings.

### Statistics

If you want a picture of how you spend your time, Orbi keeps a private
record:

- How long you've used him this session.
- How many consecutive days you've used him.
- How many pomodoros you completed today and all time.
- The applications you spend the most time in.

All of this is stored locally. Nothing is uploaded.

### Themes

Ten themes change Orbi's body colors, dome gradient, tray icon, and the
settings window itself. Pick from:

- **Classic** — BB-8 orange on cream.
- **Retro** — green LCD on dark green.
- **Hacker** — cyan on matte black.
- **Snow** — blue and white.
- **Ninja** — black body, red eyes.
- **Sunset** — warm orange and pink.
- **Ocean** — deep blues.
- **Forest** — mossy greens and gold.
- **Sakura** — soft pinks.
- **Mono** — pure grayscale, maximum contrast.

Switch themes any time from the Appearance tab. Orbi updates instantly.

### Seasonal outfits

Orbi dresses up automatically:

- **Halloween** (October 20 – 31) — a small pumpkin on his head.
- **Christmas** (December 15 – 27) — a red santa hat.
- **New Year** (December 28 – January 3) — a striped party hat.

You can pin a specific outfit or turn seasonal decoration off entirely
from the Appearance tab.

### Sizes

Three sizes: **Small**, **Medium**, and **Large**. Change in the
Appearance tab. The change is instant.

### Global hotkey

Press `Ctrl+Alt+O` from anywhere to wake Orbi up or put him to sleep. You
can change the combination or turn it off entirely from the System tab.

### Auto-start

Turn on "Start with Windows" from the System tab and Orbi launches
quietly every time you log in. No window, no taskbar entry, no console —
just a small robot rolling onto your desktop.

### Plugins

Advanced users can extend Orbi with small Python files dropped into a
folder. See the System tab → "Open plugins folder" for the location.

---

## Day-to-day use

The most common workflows, in order of how often you'll use them:

1. **Just let him sit there.** He wanders, blinks, and occasionally says
   something about what you're doing. No input needed.

2. **Boop him when you need a small win.** Left-click. He gets happy.

3. **Get stuck on a bug?** Double-click for rubber-duck mode. Explain
   the problem out loud, in text. You'll often find the answer yourself.

4. **Start a focus block.** Right-click the tray icon. 25 minutes later
   he'll tell you to take a break.

5. **Let your test suite talk to him.** Add one line to your test
   script. Every time you run tests, Orbi reacts.

6. **Watch a git repo.** Add it once. Orbi celebrates your commits from
   then on.

7. **Check in.** Right-click the tray icon to see today's pomodoro
   count, your streak, and your achievements.

---

## Getting started

1. Unzip the folder you were given.
2. Run `Orbi.exe`.
3. Orbi appears at the bottom right of your screen, and a tray icon
   appears under the `^` overflow arrow near the clock.
4. Right-click the little robot and choose **Settings** to tune him to
   your taste.
5. On the **System** tab, tick **Start with Windows** so he's always
   there.

That's it. You don't need to install anything else, and you never need
to touch a config file unless you want to.

---

## Privacy

Orbi does not connect to the internet. He does not send telemetry, does
not phone home, does not use any cloud service, and does not read your
files or your code.

Everything Orbi knows about you — your settings, your achievements, your
stats — is written to a single folder on your computer, and stays there.

If you want to see exactly what he's stored, open the config folder from
the settings window. Every file is human-readable.

To remove Orbi completely: quit him from the tray menu, then delete the
config folder and the app folder. That's everything.

---

## Frequently asked questions

**Does Orbi need Python installed?**
No. The folder you received is a self-contained application. It runs on
any Windows 10 or 11 machine without any additional software.

**Can Orbi see my code?**
No. He sees the title of your active window and the name of the program
running in it. That's how he knows when you're in VS Code or your
browser. He does not read the contents of any window.

**Can Orbi talk to me?**
No — he has no voice, only speech bubbles. He also has no microphone and
listens to nothing.

**Why is Orbi nervous about my builds?**
Because he cares. If a build takes unusually long, he starts pacing and
saying anxious things. This is meant to be comforting.

**Can I turn off the comments?**
Yes. Settings → Behaviour → uncheck "Comment on what I'm doing".

**Can I make him smaller?**
Yes. Settings → Appearance → Size → Small.

**Can I put him somewhere else on the screen?**
Drag him. He'll fall back to the bottom when you let go, but he'll fall
back to wherever you dropped him.

**Does he work on macOS or Linux?**
Not officially. The robot himself runs fine, but the window-awareness,
sound, and auto-start features are Windows-only. A version for other
platforms would need additional work.

**How do I make him stop?**
Right-click the tray icon → Quit Orbi. Or right-click him → Sleep. Sleep
hides him until you click the tray icon again.

**He's doing something weird. What do I do?**
Right-click the tray icon → Quit Orbi, then start him again. If the
problem persists, there's a log file in the config folder that will tell
you (or someone helping you) what happened.

**Can I have more than one Orbi?**
No. He's an only child. Starting him a second time just wakes up the
first one.

---

## What's inside

- **Body** — a mathematically rolled sphere with painted patches and
  seams that track his actual rotation.
- **Head** — a floating dome with a subtle orange stripe, two curved
  antennas, and eyes whose color reflects his mood.
- **Shadow** — a two-layer contact shadow that shrinks as he lifts off
  the ground.
- **Physics** — springs for his antennas and a squash-and-stretch system
  for landings, so his reactions feel weighty rather than floaty.

Everything is drawn in code. There are no image files, which is why Orbi
stays razor-sharp at any size and takes up very little disk space.

---

## Credits

Orbi was written as a personal project. He is not affiliated with any
other robot, droid, or spherical companion you may have seen.

The rubber-duck debugging concept predates Orbi by decades and belongs
to every programmer who has ever explained a bug to an inanimate object
and immediately found the answer.