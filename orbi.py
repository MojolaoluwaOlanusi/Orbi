#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orbi — a little round desktop robot that keeps you company while you code.
==========================================================================

Orbi is a frameless, always-on-top, click-through-when-not-needed desktop
companion. He rolls around the bottom of your screen (or perches on the
title bar of your active window), blinks, looks at your cursor, reacts to
your test suite, watches your git repos, reminds you to hydrate, and
occasionally says something mildly amusing.

He is drawn entirely in code. No sprites, no assets, no network calls in
the default configuration. Everything he does is deterministic and
offline-friendly, which means he works on a plane, in a locked-down
corporate environment, and behind any firewall you can imagine.

--------------------------------------------------------------------------
Design principles
--------------------------------------------------------------------------

  1. The simulation is pure Python and knows nothing about Qt.
     `Sim.update(dt, env)` is a total function of state + time + env.
     Everything visual or behavioural lives in the simulation; the Qt
     layer just paints it and forwards events. This makes the simulation
     testable without a display and makes porting to another UI toolkit
     a matter of writing one new renderer.

  2. Windows-specific code is optional and guarded. On non-Windows the
     app degrades gracefully: no foreground window detection, no idle
     detection, no sound, no autostart, but the robot still rolls.

  3. Nothing is required to be installed beyond PySide6. psutil is
     optional and enables battery/CPU awareness. Everything else is
     stdlib.

  4. Orbi never blocks his own event loop. Long operations (git polling,
     system probes) run on QTimers with generous intervals and swallow
     their own exceptions.

  5. All persisted state lives in %APPDATA%\\Orbi\\ as JSON. The file
     format is versioned and migrated on load.

--------------------------------------------------------------------------
Interaction model
--------------------------------------------------------------------------

  * The little robot on screen:
        left click      -> boop (he gets happy, small hearts pop)
        double click    -> rubber-duck debugging chat
        right click     -> settings dialog
        drag            -> pick him up; release and he falls + bounces

  * The tray icon (under the ^ overflow arrow by default):
        left click      -> wake / sleep toggle
        right click     -> quick menu (sleep, DND, dance, quit, ...)

  * From a terminal or a build script (Orbi must be running):

        orbi pass               tests passed  -> celebration
        orbi fail               tests failed  -> sad robot
        orbi build-start        build began   -> he gets nervous if it drags
        orbi build-done         build finished
        orbi build-fail         build failed  -> sad robot
        orbi say "hello"        show a speech bubble
        orbi dnd 45             do-not-disturb for 45 minutes
        orbi celebrate          party time
        orbi wake | sleep       manual control
        orbi quit               exit

    Example in a shell pipeline:

        npm test && orbi pass || orbi fail

--------------------------------------------------------------------------
Author, license, and provenance
--------------------------------------------------------------------------

    Written as a personal project. No warranty, express or implied.
    Use it, fork it, sell it, feed it to a compiler and see what comes
    out. If Orbi ever says something you disagree with, that is because
    the random module disagreed with you first.

    The body geometry uses Rodrigues' rotation formula and a small-circle
    projector so that the sphere rolls visually rather than just spinning
    a flat texture. The dome is a parametric outline; the base ring is a
    flattened ellipse. Everything else is gradients, which is what makes
    it look expensive while being free.
"""

from __future__ import annotations

# --- stdlib ----------------------------------------------------------------
import io
import json
import logging
import math
import os
import pickle
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import wave
import weakref
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# --- optional dependencies -------------------------------------------------
try:
    import psutil  # type: ignore
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

# --- Qt (required) ---------------------------------------------------------
from PySide6.QtCore import (
    Qt, QTimer, QPointF, QPoint, QRectF, QRect, QSize, QCoreApplication,
    QEvent, QObject, Signal, Slot,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient, QLinearGradient,
    QPolygonF, QPainterPath, QFont, QFontMetrics, QIcon, QPixmap, QCursor,
    QGuiApplication, QKeySequence, QShortcut, QAction,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QSystemTrayIcon, QMenu, QDialog, QVBoxLayout,
    QHBoxLayout, QFormLayout, QGridLayout, QLabel, QLineEdit, QFileDialog,
    QComboBox, QCheckBox, QPushButton, QSpinBox, QSlider, QGroupBox,
    QTabWidget, QTextEdit, QListWidget, QListWidgetItem, QProgressBar,
    QMessageBox, QScrollArea, QFrame, QSizePolicy, QToolButton,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket


# ============================================================================
# CONSTANTS
# ============================================================================

APP_NAME = "Orbi"
APP_VERSION = "2.0.0"
APP_ORG = "OrbiProject"
SERVER_NAME = "OrbiDesktopCompanion-v2"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

CLI_COMMANDS = {
    "pass", "fail", "build-start", "build-done", "build-fail",
    "say", "wake", "sleep", "celebrate", "quit", "dnd",
    "status", "focus", "relax", "love", "think", "alert", "bored"
}


# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Orbi"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "orbi.log"
STATS_FILE = CONFIG_DIR / "stats.json"
CHAT_LOG = CONFIG_DIR / "duck-chat.log"
ACHIEVE_FILE = CONFIG_DIR / "achievements.json"
PLUGINS_DIR = CONFIG_DIR / "plugins"

CFG_VERSION = 3


DEFAULTS: Dict[str, Any] = {
    # --- schema ----------------------------------------------------------
    "cfg_version": CFG_VERSION,

    # --- appearance ------------------------------------------------------
    "theme": "classic",
    "scale": 1.0,
    "seasonal": True,          # auto-decorate for halloween/christmas/etc
    "seasonal_override": "",   # "", "halloween", "christmas", "newyear", "none"

    # --- behaviour -------------------------------------------------------
    "sounds": False,
    "follow": False,
    "perch": True,
    "comments": True,
    "sys_aware": True,
    "eye_tracking": True,
    "breathing": True,
    "reactions": True,

    # --- reminders -------------------------------------------------------
    "remind_water": True,
    "remind_stretch": True,
    "remind_eyes": True,
    "remind_stand": False,
    "remind_posture": False,

    # --- git -------------------------------------------------------------
    "repos": [],
    "git_watch": True,
    "git_dirty_nudge": True,

    # --- focus -----------------------------------------------------------
    "pomo_work_min": 25,
    "pomo_break_min": 5,
    "pomo_long_break_min": 15,
    "pomo_sessions_before_long": 4,

    # --- system ----------------------------------------------------------
    "first_run_done": False,
    "hotkey_enabled": False,
    "hotkey": "Ctrl+Alt+O",
    "plugins_enabled": False,

    # --- stats -----------------------------------------------------------
    "pomo_date": "",
    "pomo_count": 0,
    "pomo_total": 0,
    "stats_enabled": True,

    # --- chat ------------------------------------------------------------
    "duck_log": False,
}


def load_config() -> Dict[str, Any]:
    """Load config, migrating older versions. Never raises."""
    cfg = dict(DEFAULTS)
    raw: Dict[str, Any] = {}
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("config read failed; using defaults")

    cfg.update(raw)

    # --- migrations ------------------------------------------------------
    v = int(cfg.get("cfg_version") or 0)
    if v < 2:
        # v1 -> v2: rename `sounds` was already there; nothing structural.
        pass
    if v < 3:
        # v2 -> v3: add stats + hotkey + seasonal defaults.
        cfg.setdefault("seasonal", DEFAULTS["seasonal"])
        cfg.setdefault("stats_enabled", DEFAULTS["stats_enabled"])
        cfg.setdefault("hotkey_enabled", DEFAULTS["hotkey_enabled"])
    cfg["cfg_version"] = CFG_VERSION

    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """Atomic-ish write. Never raises."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(CONFIG_FILE))
    except Exception:
        logging.exception("config save failed")


def load_stats() -> Dict[str, Any]:
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_stats(stats: Dict[str, Any]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    except Exception:
        logging.exception("stats save failed")


def load_achievements() -> Dict[str, Any]:
    try:
        return json.loads(ACHIEVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"unlocked": [], "counters": {}}


def save_achievements(data: Dict[str, Any]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ACHIEVE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        logging.exception("achievements save failed")


# ============================================================================
# LOGGING + CRASH HANDLING
# ============================================================================

_log_installed = False


def install_logging(level: int = logging.INFO) -> None:
    global _log_installed
    if _log_installed:
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(LOG_FILE), maxBytes=512_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root = logging.getLogger()
        root.setLevel(level)
        # avoid duplicate handlers when reloading the module
        for h in list(root.handlers):
            if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(LOG_FILE):
                root.removeHandler(h)
        root.addHandler(handler)
    except Exception:
        pass

    # also mirror warnings to stderr when launched from a console
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logging.getLogger().addHandler(sh)

    _log_installed = True


def install_excepthook() -> None:
    """Log unhandled exceptions and show a tray balloon with the log path."""
    def hook(etype, value, tb):
        try:
            logging.error(
                "Unhandled exception:\n%s",
                "".join(traceback.format_exception(etype, value, tb)),
            )
        except Exception:
            pass
        # best-effort user notification
        try:
            app = QApplication.instance()
            if app is not None and QSystemTrayIcon.supportsMessages():
                # We can't easily reach the OrbiApp instance from here, so
                # we use a one-shot tray icon.
                icon = QSystemTrayIcon(QIcon())
                icon.setToolTip("Orbi")
                icon.show()
                icon.showMessage(
                    "Orbi hit a snag",
                    "An unexpected error was logged to:\n%s" % LOG_FILE,
                    QSystemTrayIcon.MessageIcon.Warning,
                    8000,
                )
                QTimer.singleShot(9000, icon.hide)
        except Exception:
            pass
        # keep the default behaviour for debugging when a console is attached
        sys.__excepthook__(etype, value, tb)

    sys.excepthook = hook


def install_thread_excepthook() -> None:
    """Python 3.8+: catch exceptions raised in non-main threads."""
    def hook(args):
        try:
            logging.error(
                "Unhandled thread exception in %s:\n%s",
                args.thread.name if args.thread else "?",
                "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
            )
        except Exception:
            pass
    try:
        threading.excepthook = hook  # type: ignore[attr-defined]
    except Exception:
        pass


# ============================================================================
# ENUM / FLAG HELPERS
# ============================================================================

def _iv(e: Any) -> int:
    """Qt enum/flag -> int, tolerant across PySide6 versions."""
    try:
        return int(e.value)
    except AttributeError:
        return int(e)


# ============================================================================
# WINDOWS INTEROP
# ============================================================================
#
# The Windows helpers are grouped here. Everything is guarded by `if IS_WIN`
# and the public functions return safe defaults on other platforms.

SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow",
    "XamlExplorerHostIslandWindow",
}

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

HOTKEY_ID = 0xB0B1
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312


if IS_WIN:
    import ctypes
    from ctypes import wintypes as wt

    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32
    _shcore = None
    try:
        _shcore = ctypes.windll.shcore
    except Exception:
        pass
    try:
        _dwm = ctypes.windll.dwmapi
    except Exception:
        _dwm = None

    # --- function signatures --------------------------------------------
    _u32.GetForegroundWindow.restype = wt.HWND
    _u32.GetForegroundWindow.argtypes = []

    _u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _u32.GetWindowTextW.restype = ctypes.c_int

    _u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _u32.GetClassNameW.restype = ctypes.c_int

    _u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    _u32.GetWindowRect.restype = wt.BOOL

    _u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wt.DWORD

    _u32.IsWindowVisible.argtypes = [wt.HWND]
    _u32.IsWindowVisible.restype = wt.BOOL

    _u32.IsIconic.argtypes = [wt.HWND]
    _u32.IsIconic.restype = wt.BOOL

    _u32.IsZoomed.argtypes = [wt.HWND]
    _u32.IsZoomed.restype = wt.BOOL

    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.OpenProcess.restype = wt.HANDLE

    _k32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
    ]
    _k32.QueryFullProcessImageNameW.restype = wt.BOOL

    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL

    _k32.GetTickCount.argtypes = []
    _k32.GetTickCount.restype = wt.DWORD

    _u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t

    _u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

    _u32.SetWindowPos.argtypes = [
        wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wt.UINT,
    ]
    _u32.SetWindowPos.restype = wt.BOOL

    _u32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    _u32.RegisterHotKey.restype = wt.BOOL

    _u32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    _u32.UnregisterHotKey.restype = wt.BOOL

    _u32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
    _u32.GetLastInputInfo.restype = wt.BOOL

    _u32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    _u32.GetCursorPos.restype = wt.BOOL

    _u32.EnumDisplayMonitors.argtypes = [
        wt.HDC, ctypes.c_void_p, ctypes.c_void_p, wt.LPARAM,
    ]
    _u32.EnumDisplayMonitors.restype = wt.BOOL

    if _dwm:
        _dwm.DwmGetWindowAttribute.argtypes = [
            wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD,
        ]
        _dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        _dwm.DwmSetWindowAttribute.argtypes = [
            wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD,
        ]

    # --- structures -----------------------------------------------------
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.UINT),
            ("dwTime", wt.DWORD),
        ]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wt.DWORD),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wt.HWND),
            ("message", wt.UINT),
            ("wParam", wt.WPARAM),
            ("lParam", wt.LPARAM),
            ("time", wt.DWORD),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        ]


# --- process introspection --------------------------------------------------

def _process_name(pid: int) -> str:
    if not IS_WIN or not pid:
        return ""
    try:
        h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            _k32.CloseHandle(h)
    except Exception:
        pass
    return ""


def foreground_info() -> Optional[Dict[str, Any]]:
    """Active window as dict(title, exe, rect, zoomed) or None.

    `rect` is in physical pixels (top-left, bottom-right). Caller is
    responsible for dividing by devicePixelRatio if needed.
    """
    if not IS_WIN:
        return None
    try:
        hwnd = _u32.GetForegroundWindow()
        if not hwnd:
            return None
        if not _u32.IsWindowVisible(hwnd):
            return None
        if _u32.IsIconic(hwnd):
            return None

        pid = wt.DWORD(0)
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return None

        cls = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, cls, 256)
        if cls.value in SHELL_CLASSES:
            return None

        title = ctypes.create_unicode_buffer(512)
        _u32.GetWindowTextW(hwnd, title, 512)

        rect = RECT()
        ok = False
        if _dwm:
            res = _dwm.DwmGetWindowAttribute(
                hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect), ctypes.sizeof(rect),
            )
            ok = (res == 0)
        if not ok:
            _u32.GetWindowRect(hwnd, ctypes.byref(rect))

        return {
            "title": title.value,
            "exe": _process_name(pid.value),
            "rect": (rect.left, rect.top, rect.right, rect.bottom),
            "zoomed": bool(_u32.IsZoomed(hwnd)),
            "hwnd": int(hwnd),
        }
    except Exception:
        logging.exception("foreground_info failed")
        return None


def idle_seconds() -> float:
    """Seconds since the last user input event. Returns 0 on non-Windows."""
    if not IS_WIN:
        return 0.0
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not _u32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        now = _k32.GetTickCount() & 0xFFFFFFFF
        return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def cursor_pos() -> Tuple[int, int]:
    if not IS_WIN:
        return (0, 0)
    try:
        pt = wt.POINT()
        if _u32.GetCursorPos(ctypes.byref(pt)):
            return (pt.x, pt.y)
    except Exception:
        pass
    return (0, 0)


# --- window style helpers ---------------------------------------------------

def set_click_through(hwnd: int, on: bool) -> None:
    """Toggle WS_EX_TRANSPARENT so clicks pass through to the window behind."""
    if not IS_WIN or not hwnd:
        return
    try:
        st = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if on:
            new = st | WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            new = st & ~WS_EX_TRANSPARENT
        if new != st:
            _u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
    except Exception:
        logging.exception("set_click_through failed")


def make_tool_window(hwnd: int) -> None:
    """Prevent Orbi from appearing in Alt+Tab and the taskbar."""
    if not IS_WIN or not hwnd:
        return
    try:
        st = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        st = (st | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
        _u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, st)
    except Exception:
        pass


def set_dark_titlebar(hwnd: int, dark: bool = True) -> None:
    """Tell DWM to draw the window frame dark (Win10 1809+). No-op if unsupported."""
    if not IS_WIN or not hwnd or not _dwm:
        return
    try:
        val = ctypes.c_int(1 if dark else 0)
        _dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(val), ctypes.sizeof(val),
        )
    except Exception:
        pass


def windows_is_dark_mode() -> bool:
    """True if the current user prefers dark mode in Windows settings."""
    if not IS_WIN:
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as k:
            v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return int(v) == 0
    except Exception:
        return True


# --- DPI awareness ----------------------------------------------------------

def enable_dpi_awareness() -> None:
    """Best-effort per-monitor DPI awareness. Must be called before QApplication."""
    if not IS_WIN:
        return
    # Windows 10 1607+: per-monitor v2
    try:
        if _shcore is not None:
            _shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except Exception:
        pass
    # Fallback for older SDKs
    try:
        _u32.SetProcessDPIAware()
    except Exception:
        pass


# --- monitors ---------------------------------------------------------------

def enumerate_monitors() -> List[Tuple[int, int, int, int]]:
    """Return [(left, top, right, bottom), ...] for all monitors (physical px).

    On non-Windows this returns the primary screen as a single entry.
    """
    if not IS_WIN:
        return []
    monitors: List[Tuple[int, int, int, int]] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM,
    )

    def callback(hmon, hdc, lprc, data):
        try:
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcMonitor
                monitors.append((r.left, r.top, r.right, r.bottom))
            else:
                r = lprc.contents
                monitors.append((r.left, r.top, r.right, r.bottom))
        except Exception:
            pass
        return 1

    try:
        cb = MONITORENUMPROC(callback)
        _u32.EnumDisplayMonitors(None, None, cb, 0)
    except Exception:
        logging.exception("enumerate_monitors failed")

    if not monitors:
        # Fallback: use Qt if we have it
        try:
            for s in QGuiApplication.screens():
                g = s.geometry()
                monitors.append((g.left(), g.top(), g.right() + 1, g.bottom() + 1))
        except Exception:
            pass

    return monitors


# --- global hotkey ----------------------------------------------------------

class GlobalHotkey(QObject):
    """Register a system-wide hotkey via RegisterHotKey + a Qt event filter.

    On non-Windows this is a no-op object whose `register()` always returns
    False, so callers can treat it as best-effort.
    """

    activated = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._registered = False
        self._hwnd = 0
        self._mods = 0
        self._vk = 0

    def register(self, sequence: str) -> bool:
        if not IS_WIN:
            return False
        self.unregister()
        mods, vk = self._parse(sequence)
        if vk == 0:
            return False
        try:
            hwnd = int(QGuiApplication.activeWindow().winId()) if QGuiApplication.activeWindow() else 0
        except Exception:
            hwnd = 0
        # Windows hotkeys must be registered against a message-only window.
        # We use the main app's hwnd, which is fine because RegisterHotKey
        # posts WM_HOTKEY to the thread queue, not the window, and Qt's
        # native event filter sees thread messages.
        ok = bool(_u32.RegisterHotKey(hwnd, HOTKEY_ID, mods, vk))
        if ok:
            self._registered = True
            self._hwnd = hwnd
            self._mods = mods
            self._vk = vk
            try:
                QAbstractNativeEventFilter_install(self)
            except Exception:
                logging.exception("failed to install hotkey filter")
        return ok

    def unregister(self) -> None:
        if not self._registered:
            return
        try:
            _u32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
        except Exception:
            pass
        self._registered = False

    @staticmethod
    def _parse(seq: str) -> Tuple[int, int]:
        parts = [p.strip().lower() for p in re.split(r"[+\-]", seq) if p.strip()]
        mods = 0
        vk = 0
        for p in parts:
            if p in ("ctrl", "control"):
                mods |= MOD_CONTROL
            elif p == "alt":
                mods |= MOD_ALT
            elif p == "shift":
                mods |= MOD_SHIFT
            elif p in ("win", "super", "meta"):
                mods |= MOD_WIN
            else:
                # single letter or digit
                if len(p) == 1 and p.isalnum():
                    vk = ord(p.upper())
                elif p.startswith("f") and p[1:].isdigit():
                    n = int(p[1:])
                    if 1 <= n <= 24:
                        vk = 0x70 + (n - 1)  # VK_F1
        return mods, vk


if IS_WIN:
    from PySide6.QtCore import QAbstractNativeEventFilter

    class _HotkeyFilter(QAbstractNativeEventFilter):
        def __init__(self, hotkey: GlobalHotkey):
            super().__init__()
            self._hotkey_ref = weakref.ref(hotkey)

        def nativeEventFilter(self, eventType, message):  # noqa: N802
            try:
                if eventType == b"windows_generic_MSG":
                    msg = MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        hk = self._hotkey_ref()
                        if hk is not None:
                            hk.activated.emit()
                            return True, 0
            except Exception:
                pass
            return False, 0

    _hotkey_filter_singleton: Optional[_HotkeyFilter] = None

    def QAbstractNativeEventFilter_install(hotkey: GlobalHotkey) -> None:
        global _hotkey_filter_singleton
        app = QCoreApplication.instance()
        if app is None:
            return
        if _hotkey_filter_singleton is None:
            _hotkey_filter_singleton = _HotkeyFilter(hotkey)
            app.installNativeEventFilter(_hotkey_filter_singleton)
else:
    def QAbstractNativeEventFilter_install(hotkey: GlobalHotkey) -> None:  # type: ignore
        return


# --- autostart --------------------------------------------------------------

def _startup_command() -> str:
    """Return the command line Windows should run to launch Orbi."""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return '"%s" "%s"' % (exe, os.path.abspath(__file__))


def autostart_enabled() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart(on: bool) -> None:
    if not IS_WIN:
        return
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        logging.exception("set_autostart failed")


# ============================================================================
# SOUNDS
# ============================================================================

def make_wav(notes: Sequence[Tuple[Union[int, Tuple[int, int]], float]],
             vol: float = 0.22, rate: int = 22050) -> bytes:
    """Synthesize a tiny PCM wav from a list of (frequency|(f0,f1), duration)."""
    frames = bytearray()
    phase = 0.0
    for f, d in notes:
        f0, f1 = f if isinstance(f, tuple) else (f, f)
        n = max(1, int(rate * d))
        for i in range(n):
            fr = f0 + (f1 - f0) * i / n
            # 10 ms attack, 30 ms release, clipped to note length
            env = min(1.0, i / (rate * 0.01), (n - i) / (rate * 0.03))
            if fr <= 0:
                s = 0.0
            else:
                phase += 2 * math.pi * fr / rate
                # tiny harmonic for warmth
                s = math.sin(phase) * 0.8 + math.sin(phase * 2.0) * 0.2
            frames += struct.pack("<h", int(max(-1.0, min(1.0, s * env * vol)) * 32767))

    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(bytes(frames))
    w.close()
    return buf.getvalue()


SOUND_NOTES: Dict[str, Sequence[Tuple[Union[int, Tuple[int, int]], float]]] = {
    "happy":   [((600, 900), 0.07), ((900, 1200), 0.07), (0, 0.03), ((1000, 1500), 0.11)],
    "sad":     [((700, 400), 0.18), ((450, 230), 0.28)],
    "boop":    [((500, 800), 0.06), ((800, 600), 0.05)],
    "alert":   [(900, 0.06), (0, 0.04), (900, 0.06), (0, 0.04), (900, 0.06)],
    "wake":    [((400, 900), 0.10), (0, 0.03), ((900, 1300), 0.12)],
    "sleep":   [((900, 300), 0.35)],
    "commit":  [((700, 900), 0.06), ((900, 1100), 0.06), ((1100, 1300), 0.10)],
    "achievement": [((500, 700), 0.08), ((700, 900), 0.08),
                    ((900, 1100), 0.08), ((1100, 1500), 0.14)],
    "question": [(600, 0.05), (0, 0.03), ((600, 900), 0.10)],
    "cough":   [(220, 0.03), (0, 0.02), (180, 0.05)],
    "tick":    [(1200, 0.015)],
    "tock":    [(900, 0.015)],
}


class Sounds:
    """Cached winsound playback. Non-Windows or disabled -> no-op."""

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self._cache: Dict[str, bytes] = {}
        self._volume = 0.22

    def play(self, kind: str) -> None:
        if not self.enabled or not IS_WIN:
            return
        if kind not in SOUND_NOTES:
            return
        try:
            if kind not in self._cache:
                self._cache[kind] = make_wav(SOUND_NOTES[kind], vol=self._volume)
            data = self._cache[kind]
        except Exception:
            logging.exception("sound synthesis failed for %s", kind)
            return

        # winsound refuses SND_MEMORY | SND_ASYNC. SND_MEMORY requires
        # SND_SYNC, which blocks. So we run the blocking play on a
        # throwaway daemon thread and return immediately.
        t = threading.Thread(
            target=self._play_blocking,
            args=(kind, data),
            daemon=True,
        )
        t.start()

    @staticmethod
    def _play_blocking(kind: str, data: bytes) -> None:
        try:
            import winsound
            # Note: winsound has no SND_SYNC constant. Omitting SND_ASYNC
            # IS the synchronous mode. Passing SND_MEMORY alone means
            # "play these bytes, block until done". We run it on a daemon
            # thread so the UI never stalls.
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except Exception:
            logging.exception("sound playback failed for %s", kind)


# ============================================================================
# PERSONALITY  (line pools used by the sim + controller)
# ============================================================================

PASS_LINES = [
    "Tests passed! Beep boop!",
    "Green across the board!",
    "All tests pass. I knew it.",
    "Ship it! (After code review.)",
    "That's a clean run!",
    "The tests are pleased. So am I.",
    "Zero failures. Somebody's been eating their vegetables.",
    "You did it! I did nothing, but you did it.",
    "Tests are green. I'm doing my happy roll.",
    "Every test passed. Go get a coffee, you earned it.",
]

FAIL_LINES = [
    "Tests failed... it's probably a typo.",
    "Red tests. Deep breath. Read the error.",
    "Failing test. Have you tried explaining it to me?",
    "Bweeeoooop... check the stack trace.",
    "It worked on your machine, right?",
    "One or two tests are grumpy. They'll come around.",
    "Failure is just success that hasn't compiled yet.",
    "Read the assertion. It's telling you exactly what's wrong.",
    "Tests failed. The bug is between the keyboard and the chair, probably.",
    "Red. Very red. Have you tried turning it off and on again?",
]

BUILD_NERVOUS = [
    "Build's taking a while... I can't look.",
    "Still building... *nervous rolling*",
    "Is it hung? It's not hung. It's not hung.",
    "Please compile, please compile...",
    "I'm counting the seconds. I'm at... a lot of seconds.",
    "This build is doing cardio. It's fine.",
    "If it's stuck I won't say anything. But I'm thinking it.",
    "Every second of a slow build is a second I don't have to think about mine.",
]

REMINDERS = [
    ("remind_water", 60, [
        "Hydration check! Go drink some water.",
        "Water break? Your future self says thanks.",
        "Sip sip. Hydrate, human.",
        "You've been sitting a while. Water. Now.",
        "Your kidneys would like a moment of your time.",
    ]),
    ("remind_stretch", 45, [
        "Stand up and stretch for a minute!",
        "Posture check: shoulders down, back straight.",
        "Time to move. Walk around a bit?",
        "Unfold. You're not a laptop.",
        "Stretch those shoulders. I'll watch the screen.",
    ]),
    ("remind_eyes", 20, [
        "20-20-20: look at something 20 feet away for 20 seconds.",
        "Rest your eyes for a moment. I'll watch the screen.",
        "Blink. Yes, actually blink. Blinking is free.",
        "Look away from the screen. The screen will survive.",
    ]),
    ("remind_stand", 90, [
        "You've been sitting for 90 minutes. Stand up.",
        "Legs. You have them. Use them.",
        "Standing desk? If not, a quick stand will do.",
    ]),
    ("remind_posture", 30, [
        "Posture check: ears over shoulders, feet flat.",
        "Your neck is not a crane. Bring the screen closer.",
        "Chin tucked. Shoulders back. There you go.",
    ]),
]


# --- reaction-quip rules ----------------------------------------------------

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
            "opera.exe", "vivaldi.exe", "arc.exe", "chrome_proxy.exe"}

EDITORS = {
    "code.exe", "cursor.exe", "devenv.exe", "idea64.exe", "pycharm64.exe",
    "webstorm64.exe", "rider64.exe", "sublime_text.exe", "notepad++.exe",
    "zed.exe", "nvim-qt.exe", "emacs.exe", "atom.exe", "fleet.exe",
}

TERMINALS = {
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe",
    "conhost.exe", "mintty.exe", "alacritty.exe", "wezterm-gui.exe",
    "hyper.exe", "kitty.exe",
}

CHAT_APPS = {"slack.exe", "teams.exe", "ms-teams.exe", "discord.exe",
             "zoom.exe", "telegram.exe", "signal.exe", "whatsapp.exe"}

API_TOOLS = {"postman.exe", "insomnia.exe", "bruno.exe", "hoppscotch.exe"}

DOCKER_APPS = {"docker desktop.exe", "docker.exe", "podman.exe"}

MUSIC_APPS = {"spotify.exe", "musicbee.exe", "foobar2000.exe",
              "aimp.exe", "itunes.exe"}

GAME_APPS = {"steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
             "battle.net.exe", "riotclientservices.exe", "gog galaxy.exe"}


def quip_for(exe: Optional[str], title: Optional[str]) -> Optional[str]:
    """Return a one-line quip for the given foreground app, or None."""
    e = (exe or "").lower()
    t = (title or "").lower()

    if e in BROWSERS:
        if "stack overflow" in t or "stackoverflow" in t:
            return random.choice([
                "Ah, Stack Overflow. The senior engineer of us all.",
                "Copy, paste, understand. In that order. Please.",
                "The answer is in the second answer, always the second answer.",
                "Read the comments. The real answer is always in the comments.",
            ])
        if "github" in t:
            return random.choice([
                "GitHub! Any pull requests for me to cheer about?",
                "Reading code on GitHub is a form of flattery.",
                "Merge conflict incoming? I'll hold your coffee.",
                "Another repo, another rabbit hole.",
            ])
        if "youtube" in t:
            return random.choice([
                "Is this research? Totally valid research.",
                "One tutorial at 2x speed and you'll be fine.",
                "Careful, the algorithm is watching.",
            ])
        if "chatgpt" in t or "claude" in t or "gemini" in t or "copilot" in t:
            return random.choice([
                "Asking the other robots? I'm not jealous. (I'm a little jealous.)",
                "Careful, they hallucinate. I would never.",
                "One prompt away from a solution and three new problems.",
            ])
        if "reddit" in t:
            return "The front page of the internet or the front page of your afternoon?"
        if "docs." in t or "/docs/" in t or "documentation" in t:
            return "Actually reading the docs? Admirable."
        return random.choice([
            "Researching? Mind the 40 open tabs.",
            "Browser tab count: brave.",
            "One does not simply close a browser tab.",
        ])

    if e in EDITORS:
        return random.choice([
            "Code time! I'll be quietly supportive.",
            "Another day, another semicolon.",
            "Writing code... I believe in you.",
            "The bug is not on line 1. Start from line 2.",
            "Type fast, refactor faster.",
            "Remember: it's always the cache.",
        ])

    if e in TERMINALS:
        return random.choice([
            "Terminal time. Fear no command line.",
            "sudo make me a sandwich.",
            "Typing mysterious incantations, I see.",
            "`rm -rf` is not a debugging tool. (It's not.)",
            "Tab completion is a gift. Use it.",
        ])

    if e in CHAT_APPS:
        return random.choice([
            "Meetings are just bugs in the calendar.",
            "Talking to humans? Brave.",
            "An unread message is a tiny adventure.",
        ])

    if e in API_TOOLS:
        return "Poking the API. Is it alive?"

    if e in DOCKER_APPS:
        return random.choice([
            "Whale noises! Containers everywhere.",
            "It works in the container. It works on my machine. Pick one.",
        ])

    if e in MUSIC_APPS:
        return random.choice([
            "Good tunes make good code.",
            "Volume up, bugs down.",
        ])

    if e in GAME_APPS:
        return random.choice([
            "Taking a break? I'll keep the desk warm.",
            "Balance is important. Enjoy.",
        ])

    return None


# ============================================================================
# DUCK BRAIN
# ============================================================================

class DuckBrain:
    """The rubber duck. Asks the questions you'd ask yourself. No AI, no net.

    The pool is shuffled so questions don't repeat until exhausted. Keyword
    rules fire deterministically 70% of the time on turn 2+, so the duck
    feels sharp rather than random.
    """

    QUESTIONS = [
        "What did you expect to happen?",
        "And what actually happened instead?",
        "When did it last work correctly?",
        "What changed since then?",
        "Can you reproduce it every single time?",
        "What does the error message say, word for word?",
        "Walk me through the code line by line. Where do you start to doubt it?",
        "What assumption haven't you verified yet?",
        "Is it the data, the code, or the environment?",
        "If you had to bet, which line is it?",
        "Have you checked the logs?",
        "Did you save the file? (Everyone's been there.)",
        "What would you tell a colleague to look at first?",
        "Can you make the problem smaller? Remove half the code and see.",
        "What's the smallest program that still shows the bug?",
        "If you added a print statement right before the failure, what would it say?",
        "Is there a simpler version of this that works? What's different about it?",
        "What were you about to try before you came to talk to me?",
        "Have you stepped away for five minutes and come back?",
        "Is the problem actually the problem, or is it a symptom?",
        "What would happen if you deleted this code entirely?",
        "Have you tried the dumbest possible explanation first?",
        "Does it work in isolation? If yes, what's different about the context?",
        "What does the documentation say? The real documentation.",
    ]

    RULES: List[Tuple[str, List[str]]] = [
        (r"\b(null|undefined|none|nan|nullpointer|nil)\b",
         ["Where could that value be empty? Who sets it first?",
          "What happens if you assert it's not null right before use?"]),
        (r"\b(keyerror|attributeerror|nameerror|typeerror|indexerror|valueerror)\b",
         ["Which type did you expect, and which did you get?",
          "Print the type right before it blows up."]),
        (r"\b(async|await|promise|race|thread|deadlock|callback|coroutine)\b",
         ["Are you awaiting everything you should? What order do things actually run in?",
          "Two things are happening at once. Which one wins?"]),
        (r"\b(cache|cached|stale|invalidat)\b",
         ["Have you tried bypassing the cache entirely?",
          "What invalidates it, and does that actually run?"]),
        (r"\b(timezone|utc|date|time|timestamp)\b",
         ["Dates. It's always dates. Which timezone is each side in?",
          "Is it stored as UTC? Is it displayed as UTC?"]),
        (r"\b(off.?by.?one|index|loop|range|bound)\b",
         ["Count the first and last iteration by hand. What are the bounds?",
          "Is it inclusive or exclusive? Are you sure?"]),
        (r"\b(works on my machine|only on prod|only in prod|production)\b",
         ["What differs between that environment and yours? Versions, env vars, data?",
          "Can you reproduce it locally with the prod data?"]),
        (r"\b(regex|regular expression|pattern match)\b",
         ["Now you have two problems. Test it on the smallest failing string.",
          "What does the regex engine do with an empty string?"]),
        (r"\b(typo|spelling|misspell|capslock)\b",
         ["Typos: the undefeated champions of bugs. Read it out loud."]),
        (r"\b(css|flex|grid|z-index|align|center|padding|margin)\b",
         ["Open the inspector. What is the computed style actually?",
          "Have you tried a border to see if the element is where you think it is?"]),
        (r"\b(git|merge|conflict|rebase|branch|stash)\b",
         ["Breathe. git reflog remembers everything.",
          "Which branch are you actually on?"]),
        (r"\b(docker|container|image|compose|volume)\b",
         ["Is the container rebuilding, or is it using an old image?",
          "Are volumes hiding your changes?"]),
        (r"\b(sql|query|join|select|where|database|db)\b",
         ["Run the query by hand. Does it return what you expect?",
          "Is the join one-to-one or one-to-many? That changes everything."]),
        (r"\b(network|request|response|api|http|fetch|axios)\b",
         ["What does the raw response look like, before your code parses it?",
          "Is it a status code problem or a body problem?"]),
        (r"\b(import|module|package|dependency|version)\b",
         ["Which version is actually installed? pip freeze doesn't lie.",
          "Is it a circular import? They hide well."]),
        (r"\b(why)\b",
         ["That's the right question. What would have to be true for that to happen?"]),
        (r"\b(help|stuck|confused|lost)\b",
         ["You're not stuck. You're one question away from unstuck. What's the question?"]),
    ]

    EUREKA = re.compile(
        r"\b(fixed|got it|found it|solved|eureka|works now|oh!|aha|figured it out|"
        r"that's it|i see|it was)\b",
        re.I,
    )

    def __init__(self) -> None:
        self.turn = 0
        self.pool: List[str] = random.sample(self.QUESTIONS, len(self.QUESTIONS))
        self.history: List[Tuple[str, str]] = []  # (user, reply)

    def reply(self, text: str) -> Tuple[str, bool]:
        """Return (reply, eureka). Eureka means the user solved it aloud."""
        text = (text or "").strip()
        self.turn += 1
        low = text.lower()

        if self.EUREKA.search(text):
            r = random.choice([
                "You just explained your way to the answer. Duck wins!",
                "There it is. You did it, not me.",
                "See? You didn't need an answer, you needed to say it out loud.",
            ])
            self.history.append((text, r))
            return r, True

        if self.turn > 1:
            for pat, lines in self.RULES:
                if re.search(pat, low) and random.random() < 0.7:
                    r = random.choice(lines)
                    self.history.append((text, r))
                    return r, False

        if not self.pool:
            self.pool = random.sample(self.QUESTIONS, len(self.QUESTIONS))
        r = self.pool.pop()
        self.history.append((text, r))
        return r, False


# ============================================================================
# GEOMETRY / LINEAR ALGEBRA HELPERS
# ============================================================================

Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]


def normalize(v: Vec3) -> Vec3:
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / l, v[1] / l, v[2] / l)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def mat_vec(m: Mat3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )  # type: ignore[return-value]


def rodrigues(axis: Vec3, ang: float) -> Mat3:
    """Rotation matrix for `ang` radians about `axis` (Rodrigues' formula)."""
    x, y, z = normalize(axis)
    c, s = math.cos(ang), math.sin(ang)
    t = 1 - c
    return (
        (t * x * x + c,     t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c,     t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def orthonormalize(m: Mat3) -> Mat3:
    """Gram-Schmidt. Prevents drift from accumulated float error."""
    r0 = normalize(m[0])
    d = dot(m[1], r0)
    r1 = normalize((m[1][0] - d * r0[0], m[1][1] - d * r0[1], m[1][2] - d * r0[2]))
    return (r0, r1, cross(r0, r1))


def sphere_circle(n: Vec3, ang: float, steps: int = 28) -> List[Vec3]:
    """Points on a small circle of angular radius `ang` around unit vector `n`."""
    n = normalize(n)
    h: Vec3 = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = normalize(cross(n, h))
    e2 = cross(n, e1)
    ca, sa = math.cos(ang), math.sin(ang)
    pts: List[Vec3] = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        pts.append(tuple(
            n[k] * ca + sa * (ct * e1[k] + st * e2[k]) for k in range(3)
        ))  # type: ignore[arg-type]
    return pts


def great_circle(m: Vec3, steps: int = 72) -> List[Vec3]:
    return sphere_circle(m, math.pi / 2, steps)


def project_patch(pts: List[Vec3], M: Mat3, R: float) -> Optional[List[Tuple[float, float]]]:
    """Return a filled-patch polygon in px. Back-facing pts clamp to the rim."""
    rot = [mat_vec(M, p) for p in pts]
    if all(z <= 0.0 for _, _, z in rot):
        return None
    out: List[Tuple[float, float]] = []
    for x, y, z in rot:
        if z < 0:
            h = math.hypot(x, y) or 1.0
            x, y = x / h, y / h
        out.append((x * R, y * R))
    return out


def visible_runs(pts: List[Vec3], M: Mat3, R: float) -> List[List[Tuple[float, float]]]:
    """Return the front-facing arcs of a great/small circle as polyline runs."""
    rot = [mat_vec(M, p) for p in pts]
    rot.append(rot[0])
    runs: List[List[Tuple[float, float]]] = []
    cur: List[Tuple[float, float]] = []
    for x, y, z in rot:
        if z > 0.03:
            cur.append((x * R, y * R))
        else:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
    if len(cur) > 1:
        runs.append(cur)
    return runs


def dome_outline(r: float, steps: int = 24, bulge: float = 0.22) -> List[Tuple[float, float]]:
    """Parametric outline of the front-facing dome silhouette."""
    pts: List[Tuple[float, float]] = [
        (-r * math.cos(math.pi * i / steps), -r * math.sin(math.pi * i / steps))
        for i in range(steps + 1)
    ]
    for i in range(1, steps):
        t = i / steps
        pts.append((r - 2 * r * t, bulge * r * math.sin(math.pi * t)))
    return pts


def dome_band(r: float, y1: float, y2: float, steps: int = 28) -> List[Tuple[float, float]]:
    """Orange stripe across the dome between heights y1 < y2 (negative = up)."""
    def curve(x: float, y0: float) -> float:
        return y0 + 0.10 * r * math.sqrt(max(0.0, 1 - (x / r) ** 2))

    xmax = math.sqrt(max(0.0, r * r - y2 * y2))
    xs = [(-xmax + 2 * xmax * i / steps) for i in range(steps + 1)]
    upper: List[Tuple[float, float]] = []
    for x in xs:
        top = -math.sqrt(max(0.0, r * r - x * x))
        upper.append((x, max(top, curve(x, y1))))
    lower = [(x, curve(x, y2)) for x in reversed(xs)]
    return upper + lower


def heart_points(size: float, n: int = 26) -> List[Tuple[float, float]]:
    """Heart curve, scaled so the widest point is `size`."""
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x * size / 34.0, y * size / 34.0))
    return pts


def star_points(size: float, points: int = 5, inner: float = 0.42) -> List[Tuple[float, float]]:
    """N-pointed star. `inner` is inner/outer radius ratio."""
    pts: List[Tuple[float, float]] = []
    n = points * 2
    for i in range(n):
        r = size if i % 2 == 0 else size * inner
        a = -math.pi / 2 + i * math.pi / points
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


# ============================================================================
# THEMES
# ============================================================================

THEMES: Dict[str, Dict[str, Any]] = {
    "classic": {
        "label": "Classic (BB-8 orange)",
        "orange": (238, 118, 34), "dark": (52, 56, 64),
        "silver": (176, 176, 186), "light": (226, 226, 232),
        "body_hi": (255, 255, 255), "body_mid": (233, 229, 218), "body_lo": (168, 162, 150),
        "dome_hi": (255, 255, 255), "dome_mid": (228, 225, 216), "dome_lo": (160, 156, 148),
        "bg": (31, 36, 48), "fg": (230, 233, 242),
    },
    "retro": {
        "label": "Retro (green LCD)",
        "orange": (110, 180, 80), "dark": (40, 60, 35),
        "silver": (150, 170, 130), "light": (200, 215, 170),
        "body_hi": (228, 238, 205), "body_mid": (185, 205, 160), "body_lo": (118, 140, 100),
        "dome_hi": (235, 245, 210), "dome_mid": (195, 215, 170), "dome_lo": (128, 150, 108),
        "bg": (24, 34, 24), "fg": (180, 220, 160),
    },
    "hacker": {
        "label": "Hacker (cyan on black)",
        "orange": (0, 220, 200), "dark": (18, 18, 22),
        "silver": (95, 105, 115), "light": (200, 230, 235),
        "body_hi": (110, 120, 130), "body_mid": (55, 60, 70), "body_lo": (18, 20, 26),
        "dome_hi": (120, 130, 140), "dome_mid": (60, 65, 75), "dome_lo": (20, 22, 28),
        "bg": (10, 12, 16), "fg": (0, 230, 210),
    },
    "snow": {
        "label": "Snow (white & blue)",
        "orange": (130, 180, 255), "dark": (60, 90, 130),
        "silver": (190, 210, 235), "light": (240, 246, 255),
        "body_hi": (255, 255, 255), "body_mid": (235, 242, 250), "body_lo": (170, 190, 215),
        "dome_hi": (255, 255, 255), "dome_mid": (235, 242, 250), "dome_lo": (170, 190, 215),
        "bg": (28, 36, 52), "fg": (230, 240, 255),
    },
    "ninja": {
        "label": "Ninja (matte black & red)",
        "orange": (200, 40, 50), "dark": (20, 20, 22),
        "silver": (80, 80, 90), "light": (140, 140, 150),
        "body_hi": (92, 92, 102), "body_mid": (46, 46, 56), "body_lo": (18, 18, 22),
        "dome_hi": (100, 100, 110), "dome_mid": (50, 50, 60), "dome_lo": (20, 20, 25),
        "bg": (14, 14, 16), "fg": (220, 220, 230),
    },
    "sunset": {
        "label": "Sunset (warm gradient)",
        "orange": (255, 140, 60), "dark": (120, 50, 40),
        "silver": (230, 180, 140), "light": (255, 220, 180),
        "body_hi": (255, 240, 220), "body_mid": (250, 200, 170), "body_lo": (200, 130, 100),
        "dome_hi": (255, 245, 230), "dome_mid": (250, 205, 175), "dome_lo": (200, 135, 105),
        "bg": (50, 30, 40), "fg": (255, 230, 210),
    },
    "ocean": {
        "label": "Ocean (deep blues)",
        "orange": (70, 180, 210), "dark": (30, 60, 90),
        "silver": (120, 160, 190), "light": (180, 215, 235),
        "body_hi": (220, 235, 245), "body_mid": (170, 205, 225), "body_lo": (100, 140, 170),
        "dome_hi": (225, 240, 250), "dome_mid": (180, 215, 235), "dome_lo": (105, 145, 175),
        "bg": (18, 30, 48), "fg": (200, 230, 245),
    },
    "forest": {
        "label": "Forest (mossy greens)",
        "orange": (200, 170, 70), "dark": (40, 60, 40),
        "silver": (140, 160, 130), "light": (210, 220, 190),
        "body_hi": (225, 235, 205), "body_mid": (180, 200, 160), "body_lo": (115, 140, 105),
        "dome_hi": (230, 240, 210), "dome_mid": (185, 205, 165), "dome_lo": (120, 145, 110),
        "bg": (24, 36, 28), "fg": (210, 230, 195),
    },
    "sakura": {
        "label": "Sakura (soft pinks)",
        "orange": (245, 130, 165), "dark": (110, 55, 75),
        "silver": (215, 180, 200), "light": (250, 220, 235),
        "body_hi": (255, 245, 250), "body_mid": (250, 220, 235), "body_lo": (205, 155, 180),
        "dome_hi": (255, 245, 250), "dome_mid": (250, 225, 240), "dome_lo": (208, 158, 183),
        "bg": (48, 30, 42), "fg": (250, 220, 235),
    },
    "mono": {
        "label": "Mono (grayscale, high contrast)",
        "orange": (220, 220, 220), "dark": (40, 40, 40),
        "silver": (140, 140, 140), "light": (200, 200, 200),
        "body_hi": (240, 240, 240), "body_mid": (190, 190, 190), "body_lo": (120, 120, 120),
        "dome_hi": (245, 245, 245), "dome_mid": (200, 200, 200), "dome_lo": (130, 130, 130),
        "bg": (20, 20, 20), "fg": (230, 230, 230),
    },
}


# Module-level mutable theme dict + cached body-geometry patch list.
CUR_THEME: Dict[str, Any] = dict(THEMES["classic"])
ORANGE: Tuple[int, int, int] = CUR_THEME["orange"]
DARK: Tuple[int, int, int] = CUR_THEME["dark"]
SILVER: Tuple[int, int, int] = CUR_THEME["silver"]
LIGHT: Tuple[int, int, int] = CUR_THEME["light"]


# The set of visual patch layers on the sphere. Each entry is
# (surface-normal, [(angular-radius, theme-color-key), ...]).
_PATCH_DEFS: List[Tuple[Vec3, List[Tuple[float, str]]]] = [
    ((0.15, -0.05, 1.0), [(0.64, "orange"), (0.52, "dark"),
                          (0.40, "light"), (0.24, "orange"), (0.11, "dark")]),
    ((-0.95, 0.20, 0.25), [(0.32, "silver"), (0.22, "orange")]),
    ((0.85, 0.45, 0.30), [(0.27, "silver"), (0.18, "dark")]),
    ((0.10, 0.97, 0.20), [(0.36, "orange"), (0.21, "dark")]),
    ((-0.25, -0.95, 0.20), [(0.25, "silver"), (0.16, "orange")]),
    ((0.10, 0.10, -1.0), [(0.62, "orange"), (0.42, "dark"), (0.22, "orange")]),
    ((0.70, -0.60, -0.40), [(0.31, "silver"), (0.20, "orange")]),
    ((-0.70, -0.50, -0.50), [(0.29, "orange"), (0.17, "dark")]),
]

BODY_PATCHES: List[Tuple[Vec3, List[Tuple[List[Vec3], Tuple[int, int, int]]]]] = []
BODY_SEAMS: List[List[Vec3]] = [
    great_circle(normalize(m))
    for m in ((1.0, 0.2, 0.3), (0.2, 1.0, 0.1), (0.6, -0.7, 0.4))
]
BODY_BAND: List[Vec3] = great_circle(normalize((0.35, 0.9, 0.2)))


def rebuild_body_visuals(theme_name: str) -> None:
    """Rebuild the cached geometry + colors for the given theme."""
    global CUR_THEME, ORANGE, DARK, SILVER, LIGHT, BODY_PATCHES
    CUR_THEME = dict(THEMES.get(theme_name) or THEMES["classic"])
    ORANGE = CUR_THEME["orange"]
    DARK = CUR_THEME["dark"]
    SILVER = CUR_THEME["silver"]
    LIGHT = CUR_THEME["light"]

    patches: List[Tuple[Vec3, List[Tuple[List[Vec3], Tuple[int, int, int]]]]] = []
    for n, layers in _PATCH_DEFS:
        vn = normalize(n)
        built: List[Tuple[List[Vec3], Tuple[int, int, int]]] = []
        for ang, key in layers:
            color = tuple(CUR_THEME.get(key, (128, 128, 128)))
            built.append((sphere_circle(vn, ang, 26), color))
        patches.append((vn, built))
    BODY_PATCHES = patches


rebuild_body_visuals("classic")


# ============================================================================
# SEASONAL DECORATION
# ============================================================================

def current_season() -> str:
    """Return 'halloween', 'christmas', 'newyear', or ''."""
    try:
        now = datetime.now()
        if now.month == 10 and now.day >= 20:
            return "halloween"
        if now.month == 12 and now.day >= 15:
            return "christmas"
        if now.month == 1 and now.day <= 3:
            return "newyear"
        if now.month == 12 and now.day >= 28:
            return "newyear"
    except Exception:
        pass
    return ""


# ============================================================================
# TIME-OF-DAY TINT
# ============================================================================

def time_of_day_tint() -> Tuple[int, int, int, int]:
    """Return (r, g, b, a) overlay to blend over Orbi. Subtle."""
    h = datetime.now().hour
    # morning (6-10): warm
    # midday (10-16): neutral
    # evening (16-20): warm orange
    # night (20-6): cool blue, subtle
    if 6 <= h < 10:
        return (255, 200, 130, 18)
    if 10 <= h < 16:
        return (255, 255, 255, 0)
    if 16 <= h < 20:
        return (255, 160, 90, 22)
    return (90, 130, 220, 26)


# ============================================================================
# MOODS, PARTICLES, ENV
# ============================================================================

MOOD_COLORS: Dict[str, Tuple[int, int, int]] = {
    "normal":  (120, 205, 255),
    "happy":   (130, 255, 175),
    "sad":     (140, 150, 255),
    "sleepy":  (70, 85, 115),
    "panic":   (255, 80, 70),
    "listen":  (255, 205, 90),
    "alert":   (255, 245, 170),
    "love":    (255, 150, 200),
    "thinking":(200, 220, 255),
    "bored":   (140, 150, 180),
}

CONFETTI_COLORS = [
    (255, 99, 99), (255, 200, 60), (90, 220, 130),
    (90, 170, 255), (200, 120, 255), (255, 140, 40),
]


class Env:
    """Everything the sim needs to know about the outside world this frame.

    Kept as a tiny value object so `Sim.update` stays pure and testable.
    """
    __slots__ = ("work", "cursor", "perch", "typing")

    def __init__(
        self,
        work: Tuple[int, int, int, int] = (0, 0, 1920, 1040),
        cursor: Tuple[int, int] = (0, 0),
        perch: Optional[Tuple[int, int, int]] = None,
        typing: bool = False,
    ) -> None:
        self.work = work
        self.cursor = cursor
        self.perch = perch
        self.typing = typing


# ============================================================================
# SIM
# ============================================================================
#
# The simulation is a pure state machine. It has no Qt dependencies and can
# be ticked at any dt from a test harness. The Qt layer calls `update(dt,
# env)` every frame and paints from the resulting state.
#
# States (mode):
#     ground  - rolling on a surface, wandering, idling, celebrating, etc.
#     hop     - interpolating along an arc between two points
#     drag    - the user is holding Orbi
#     fall    - free-falling after being dropped
#
# Behaviours (behavior, only meaningful in ground mode):
#     idle    - nothing special; random little movements
#     wander  - rolling toward `wander_to`
#     spin    - celebrating; oscillating left/right fast
#     panic   - build running too long; small shakes
#     sleep   - transitioning into or out of sleep
#     think   - "thinking" pose with breathing and thought bubbles
#     peek    - pause, look at cursor, resume
#

class Sim:
    """Pure simulation of Orbi's body, head, mood, and particles."""

    def __init__(self, scale: float = 1.0) -> None:
        self.set_scale(scale)

        # --- body position + motion --------------------------------------
        self.x: float = 400.0
        self.y: float = 800.0
        self.vx: float = 0.0
        self.vy: float = 0.0
        self.ground: float = 800.0
        self.mode: str = "ground"
        self.hop: Optional[Dict[str, Any]] = None
        self.hop_h: float = 0.0
        self.bounce: float = 0.0
        self.range: Tuple[float, float] = (0.0, 1e9)
        self.work: Tuple[int, int, int, int] = (0, 0, 1920, 1040)

        # --- rolling orientation -----------------------------------------
        self.orient: Mat3 = mat_mul(
            rodrigues((1, 0, 0), -0.25),
            rodrigues((0, 1, 0), 0.35),
        )
        self._roll_axis: Vec3 = normalize((0.0, math.sin(0.3), math.cos(0.3)))
        self._roll_n: int = 0

        # --- head --------------------------------------------------------
        self.tilt: float = 0.0
        self.tilt_v: float = 0.0
        self.ant: float = 0.0          # antenna bend (-1..1)
        self.ant_v: float = 0.0
        self.drop: float = 0.0

        # --- squash & stretch --------------------------------------------
        self.squash: float = 1.0
        self.squash_v: float = 0.0

        # --- look --------------------------------------------------------
        self.look: List[float] = [0.0, 0.0]
        self.look_target: List[float] = [0.0, 0.0]
        self.next_look: float = 1.5

        # --- time + mood -------------------------------------------------
        self.t: float = 0.0
        self._dt: float = 0.016
        self.mood: str = "normal"
        self.mood_until: float = 0.0

        # --- blink -------------------------------------------------------
        self.blink: float = 0.0
        self.blink_phase: Optional[float] = None
        self.next_blink: float = 2.0

        # --- behavior ----------------------------------------------------
        self.behavior: str = "idle"
        self.behavior_until: float = 0.0
        self.idle_timer: float = 2.0
        self.wander_to: Optional[float] = None

        # --- speech ------------------------------------------------------
        self.bubble: Optional[Dict[str, Any]] = None
        self.queue: List[Dict[str, Any]] = []

        # --- particles ---------------------------------------------------
        self.particles: List[Dict[str, Any]] = []
        self.trail_cd: float = 0.0

        # --- sleep -------------------------------------------------------
        self.sleep_p: float = 0.0
        self.sleep_dir: int = 0
        self.on_asleep: Optional[Callable[[], None]] = None

        # --- flags (set by the controller) -------------------------------
        self.drowsy: bool = False
        self.nervous: bool = False
        self.focus: bool = False
        self.listening: bool = False
        self.follow: bool = False
        self.perch_enabled: bool = True
        self.breathing: bool = True
        self.eye_tracking: bool = True
        self.reactions: bool = True

        # --- perch tracking ----------------------------------------------
        self.perch_key: Optional[Tuple[int, ...]] = None
        self.perch_since: float = 0.0

        # --- typing rhythm -----------------------------------------------
        # A tiny "typing intensity" estimator. Rises when idle_seconds
        # is small and falling fast; used to sway the head subtly.
        self.typing_energy: float = 0.0
        self._last_idle: float = 0.0

        # --- reaction bubbles --------------------------------------------
        self.reaction: Optional[Dict[str, Any]] = None

        # --- thought dots ------------------------------------------------
        self.thinking: bool = False
        self.thinking_until: float = 0.0
        self._think_phase: float = 0.0

    # ------------------------------------------------------------------
    # basics
    # ------------------------------------------------------------------
    def set_scale(self, scale: float) -> None:
        self.s: float = float(scale)
        self.R: float = 34.0 * self.s
        self.total_h: float = 3.3 * self.R

    def mood_now(self) -> str:
        if self.mode == "drag":
            return "alert"
        if self.t < self.mood_until:
            return self.mood
        if self.thinking:
            return "thinking"
        if self.sleep_p > 0.05 or self.drowsy:
            return "sleepy"
        if self.nervous:
            return "panic"
        if self.listening:
            return "listen"
        # detect long boredom: no user input for a while but not yet drowsy
        if self.behavior == "idle" and self.idle_timer > 3.0 and self.typing_energy < 0.1:
            return "bored"
        return "normal"

    def set_mood(self, mood: str, dur: float = 2.0) -> None:
        self.mood, self.mood_until = mood, self.t + dur

    def eye_open(self) -> float:
        o = 1.0 - self.blink
        if self.drowsy:
            o *= 0.45 + 0.10 * math.sin(self.t * 0.9)
        if self.thinking:
            o *= 0.85 + 0.05 * math.sin(self.t * 3.0)
        return max(0.0, o * (1.0 - self.sleep_p))

    def bounce_px(self) -> float:
        return self.bounce * 0.30 * self.R * abs(math.sin(self.t * 16.0))

    def ant_value(self) -> float:
        """Antenna bend, with an extra panic jitter layered on top."""
        extra = math.sin(self.t * 38.0) * 0.25 if self.mood_now() == "panic" else 0.0
        return self.ant + extra

    def breathing_offset(self) -> float:
        """Subtle vertical breathing displacement, in px."""
        if not self.breathing:
            return 0.0
        return math.sin(self.t * 1.6) * 0.6 * self.s

    def squash_xy(self) -> Tuple[float, float]:
        """Return (sx, sy) multipliers for the body, anchored at contact."""
        sq = self.squash if abs(self.squash - 1.0) > 0.005 else 1.0
        return (1.0 / sq, sq)

    # ------------------------------------------------------------------
    # speech
    # ------------------------------------------------------------------
    def say(self, text: str, dur: Optional[float] = None, urgent: bool = False) -> None:
        if not text:
            return
        if dur is None:
            # base duration + per-char reading time, clamped
            dur = max(2.8, 1.6 + 0.06 * len(text))
        if urgent or self.bubble is None:
            self.bubble = {"text": text, "dur": dur, "t0": self.t}
            if urgent:
                self.queue.clear()
        elif len(self.queue) < 3:
            self.queue.append({"text": text, "dur": dur})

    def bubble_alpha(self) -> float:
        b = self.bubble
        if not b:
            return 0.0
        age = self.t - b["t0"]
        # 0.2s fade in, 0.4s fade out, clamped
        return max(0.0, min(1.0, age / 0.2, (b["dur"] - age) / 0.4))

    def react(self, glyph: str, dur: float = 1.0) -> None:
        """Show a quick '!' or '?' above the head."""
        if not self.reactions:
            return
        self.reaction = {"glyph": glyph, "t0": self.t, "dur": dur}

    def reaction_state(self) -> Optional[Tuple[str, float, float]]:
        """Return (glyph, alpha, scale) or None."""
        r = self.reaction
        if not r:
            return None
        age = self.t - r["t0"]
        if age > r["dur"]:
            self.reaction = None
            return None
        # pop in over 0.15s, hold, fade out over last 0.3s
        if age < 0.15:
            s = age / 0.15
        else:
            s = 1.0
        a = min(1.0, age / 0.1, (r["dur"] - age) / 0.3)
        return (r["glyph"], max(0.0, a), 0.6 + 0.4 * s)

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def confetti(self, n: int = 36) -> None:
        cx, cy = self.x, self.y - self.R * 2.2 - self.hop_h
        for _ in range(n):
            a = random.uniform(-math.pi * 0.95, -math.pi * 0.05)
            sp = random.uniform(140, 380) * math.sqrt(self.s)
            life = random.uniform(1.0, 1.8)
            self.particles.append({
                "k": "c", "x": cx, "y": cy,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                "life": life, "l0": life,
                "c": random.choice(CONFETTI_COLORS),
                "r": random.uniform(0, 6.28), "vr": random.uniform(-9, 9),
            })

    def stars(self, n: int = 8) -> None:
        cx, cy = self.x, self.y - self.R * 2.2 - self.hop_h
        for _ in range(n):
            a = random.uniform(-math.pi, 0)
            sp = random.uniform(120, 320) * math.sqrt(self.s)
            life = random.uniform(0.8, 1.4)
            self.particles.append({
                "k": "s", "x": cx, "y": cy,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                "life": life, "l0": life,
                "r": random.uniform(0, 6.28), "vr": random.uniform(-8, 8),
                "size": random.uniform(4.0, 8.0) * self.s,
            })

    def hearts(self, n: int = 3) -> None:
        for i in range(n):
            self.particles.append({
                "k": "h",
                "x": self.x + random.uniform(-0.5, 0.5) * self.R,
                "y": self.y - self.R * 2.6 - self.hop_h,
                "vx": random.uniform(-14, 14),
                "vy": random.uniform(-80, -55),
                "life": 1.2 + 0.15 * i, "l0": 1.35,
            })

    def celebrate(self, text: Optional[str] = None, seconds: float = 2.4) -> None:
        self.behavior, self.behavior_until = "spin", self.t + seconds
        self.set_mood("happy", seconds + 0.8)
        self.confetti()
        self.bounce = 1.0
        self.squash_v -= 3.5
        if text:
            self.say(text, urgent=True)

    def sad(self, text: Optional[str] = None, seconds: float = 4.0) -> None:
        self.set_mood("sad", seconds)
        self.behavior, self.idle_timer = "idle", seconds
        if text:
            self.say(text, urgent=True)

    def love(self, text: Optional[str] = None, seconds: float = 2.0) -> None:
        self.set_mood("love", seconds)
        self.hearts(4)
        if text:
            self.say(text, urgent=True)

    def poke(self) -> None:
        self.set_mood("happy", 1.3)
        self.bounce = 1.0
        self.squash_v -= 4.0
        self.hearts(3)
        self.react("!")
        if random.random() < 0.4:
            self.say(random.choice([
                "Beep!", "Boop!", "Bweeeep?", "*happy rolling noises*",
                "Hey! That tickles.", "Ooh. Again?",
            ]), 1.8)

    def begin_think(self, dur: float = 3.0) -> None:
        """Enter a 'thinking' pose with animated dots."""
        self.thinking = True
        self.thinking_until = self.t + dur
        self._think_phase = 0.0

    def nap(self, callback: Optional[Callable[[], None]]) -> None:
        self.sleep_dir, self.on_asleep = 1, callback
        self.behavior, self.mood_until = "sleep", 0.0
        self.bubble, self.queue = None, []

    def wake(self) -> None:
        self.sleep_dir, self.drowsy = -1, False
        self.set_mood("happy", 1.6)
        self.behavior, self.idle_timer = "idle", 2.0
        self.thinking = False

    def begin_drag(self) -> None:
        self.mode, self.hop, self.hop_h, self.vy = "drag", None, 0.0, 0.0

    def drag_to(self, x: float, y: float) -> None:
        l, t, r, b = self.work
        nx = min(max(x, l + self.R), r - self.R)
        ny = min(y, b)
        # smoothed velocity so releasing feels right
        self.vx = 0.6 * self.vx + 0.4 * (nx - self.x) / max(self._dt, 0.016)
        self.x, self.y = nx, ny

    def end_drag(self) -> None:
        self.mode, self.vy = "fall", 0.0

    # ------------------------------------------------------------------
    # internal: rolling
    # ------------------------------------------------------------------
    def _roll(self, dx: float) -> None:
        if dx:
            self.orient = mat_mul(rodrigues(self._roll_axis, dx / self.R), self.orient)
            self._roll_n += 1
            if self._roll_n % 120 == 0:
                self.orient = orthonormalize(self.orient)

    # ------------------------------------------------------------------
    # internal: updaters
    # ------------------------------------------------------------------
    def _update_squash(self, dt: float) -> None:
        # critically damped spring back to 1.0
        self.squash_v += (1.0 - self.squash) * 260.0 * dt - self.squash_v * 14.0 * dt
        self.squash += self.squash_v * dt
        self.squash = max(0.62, min(1.40, self.squash))

    def _update_sleep(self, dt: float) -> None:
        if self.sleep_dir > 0:
            self.sleep_p = min(1.0, self.sleep_p + dt / 1.3)
            if self.sleep_p >= 1.0:
                self.sleep_dir = 0
                cb, self.on_asleep = self.on_asleep, None
                if cb:
                    try:
                        cb()
                    except Exception:
                        logging.exception("sleep callback failed")
        elif self.sleep_dir < 0:
            self.sleep_p = max(0.0, self.sleep_p - dt / 0.6)
            if self.sleep_p <= 0.0:
                self.sleep_dir = 0

    def _update_particles(self, dt: float) -> None:
        keep: List[Dict[str, Any]] = []
        for q in self.particles:
            q["life"] -= dt
            if q["life"] <= 0:
                continue
            k = q["k"]
            if k == "c":
                q["vy"] += 700 * dt
                q["vx"] *= 0.99
                q["r"] += q["vr"] * dt
            elif k == "s":
                q["vy"] += 400 * dt
                q["r"] += q["vr"] * dt
                q["vx"] *= 0.97
            elif k == "d":
                q["vy"] -= 30 * dt
                q["vx"] *= 0.96
            # "h" hearts just float
            q["x"] += q["vx"] * dt
            q["y"] += q["vy"] * dt
            keep.append(q)
        self.particles = keep

    def _update_bubble(self) -> None:
        b = self.bubble
        if b and self.t - b["t0"] > b["dur"]:
            self.bubble = None
            if self.queue:
                n = self.queue.pop(0)
                self.bubble = {"text": n["text"], "dur": n["dur"], "t0": self.t}

    def _update_blink(self, dt: float) -> None:
        self.next_blink -= dt
        if self.next_blink <= 0 and self.blink_phase is None:
            self.blink_phase = 0.0
            self.next_blink = random.uniform(2.0, 5.0)
        if self.blink_phase is not None:
            self.blink_phase += dt / 0.16
            if self.blink_phase >= 1.0:
                self.blink_phase, self.blink = None, 0.0
            else:
                self.blink = math.sin(math.pi * self.blink_phase)

    def _resolve_perch(self, env: Env) -> Optional[Tuple[int, int, int]]:
        # Debounce perch changes so a jittery foreground window doesn't
        # cause Orbi to hop every frame.
        key = None if env.perch is None else tuple(int(v / 4) for v in env.perch)
        if key != self.perch_key:
            self.perch_key, self.perch_since = key, self.t
        if key is None or not self.perch_enabled or self.t - self.perch_since < 0.9:
            return None
        return env.perch

    def _start_hop(self, tx: float, ty: float) -> None:
        dist = math.hypot(tx - self.x, ty - self.y)
        self.hop = {
            "sx": self.x, "sy": self.y, "tx": tx, "ty": ty,
            "t0": self.t,
            "dur": 0.5 + min(0.6, dist / 1500.0),
            "arc": 60 * self.s + min(140 * self.s, abs(ty - self.y) * 0.25),
        }
        self.mode, self.vx = "hop", 0.0
        self.squash_v -= 3.0

    def _update_hop(self) -> None:
        h = self.hop
        if h is None:
            self.mode = "ground"
            return
        p = (self.t - h["t0"]) / h["dur"]
        if p >= 1.0:
            self.x, self.y = h["tx"], h["ty"]
            self.ground = self.y
            self.hop = None
            self.hop_h = 0.0
            self.mode = "ground"
            self.bounce = 0.5
            self.squash_v += 6.0
            return
        e = p * p * (3 - 2 * p)  # smoothstep
        nx = h["sx"] + (h["tx"] - h["sx"]) * e
        self.y = h["sy"] + (h["ty"] - h["sy"]) * e
        self.hop_h = h["arc"] * math.sin(math.pi * p)
        self._roll(nx - self.x)
        self.x = nx

    def _update_fall(self, dt: float, env: Env) -> None:
        l, t_, r, b = env.work
        self.vy += 1900 * self.s * dt
        self.x = min(max(self.x + self.vx * dt, l + self.R), r - self.R)
        self.vx *= 0.98
        if self.y > b + 2:
            self.y = b
        ny = self.y + self.vy * dt

        surfaces: List[float] = [b]
        p = env.perch
        if p and self.perch_enabled and p[1] >= env.work[1] \
                and p[0] <= self.x <= p[2] and p[1] < b - 20:
            surfaces.append(p[1])

        landed: Optional[float] = None
        for sy in sorted(surfaces):
            if self.y <= sy + 2 and ny >= sy:
                landed = sy
                break
        if landed is None:
            self.y = ny
            return

        self.y = self.ground = landed
        if abs(self.vy) > 420 * self.s:
            self.vy = -self.vy * 0.32
            self.squash_v += min(9.0, abs(self.vy) / 90.0)
        else:
            self.vy = 0.0
            self.mode = "ground"
            self.hop_h = 0.0
            self.bounce = 0.8
            self.squash_v += 5.0
            self.vx *= 0.5

    def _update_ground(self, dt: float, env: Env) -> None:
        l, t_, r, b = env.work
        R, s = self.R, self.s
        lo, hi = l + R + 8, r - R - 8

        desired = float(b)
        perch = self._resolve_perch(env)
        if perch is not None:
            pl, pt, pr = perch
            if pt - t_ >= self.total_h + 70 and pr - pl >= 4 * R:
                desired = float(pt)
                lo, hi = max(lo, pl + R), min(hi, pr - R)
        if hi < lo:
            hi = lo
        self.range = (lo, hi)

        diff = desired - self.ground
        if abs(diff) > 6:
            self._start_hop(min(max(self.x, lo), hi), desired)
            return
        if diff:
            self.ground = self.y = desired

        # ---- behavior dispatch -----------------------------------------
        now = self.t
        beh = self.behavior
        if self.sleep_p > 0 or self.sleep_dir != 0:
            beh = "sleep"
        elif self.nervous:
            beh = "panic"
        elif self.thinking and now >= self.thinking_until:
            self.thinking = False
        elif beh == "spin" and now >= self.behavior_until:
            self.behavior, beh, self.idle_timer = "idle", "idle", 1.5

        tvx = 0.0
        if beh == "spin":
            tvx = 210 * s * math.sin(now * 8.0)
        elif beh == "panic":
            tvx = 95 * s * math.sin(now * 6.5)
        elif beh == "sleep" or self.listening or self.drowsy:
            tvx = 0.0
        elif self.follow and not self.focus:
            dx = env.cursor[0] - self.x
            if abs(dx) > 110 * s:
                mag = min(1.0, max(0.35, abs(dx) / (250 * s))) * 170 * s
                tvx = math.copysign(mag, dx)
        elif beh == "wander" and self.wander_to is not None:
            dx = self.wander_to - self.x
            if abs(dx) < 6 * s:
                self.behavior, self.wander_to, self.idle_timer = "idle", None, random.uniform(2, 6)
            else:
                mag = min(1.0, abs(dx) / (80 * s) + 0.25) * 120 * s
                tvx = math.copysign(mag, dx)
        else:
            self.idle_timer -= dt
            if self.idle_timer <= 0:
                self._pick_idle(lo, hi)

        # typed-in typing energy (tracks fast mouse/key activity)
        if env.typing:
            self.typing_energy = min(1.0, self.typing_energy + dt * 3.0)
        else:
            self.typing_energy = max(0.0, self.typing_energy - dt * 0.6)

        # integrate x
        step = 900 * s * dt
        self.vx += max(-step, min(step, tvx - self.vx))
        nx = self.x + self.vx * dt
        if nx < lo:
            nx, self.vx = lo, 0.0
        elif nx > hi:
            nx, self.vx = hi, 0.0
        self._roll(nx - self.x)
        self.x = nx
        self.y = self.ground

        # motion trail
        if abs(self.vx) > 90 * s:
            self.trail_cd -= dt
            if self.trail_cd <= 0:
                self.trail_cd = 0.05
                self.particles.append({
                    "k": "d",
                    "x": self.x - math.copysign(R * 0.75, self.vx),
                    "y": self.y - 2 * s,
                    "vx": -self.vx * 0.08, "vy": -25 * s,
                    "life": 0.4, "l0": 0.4,
                    "r": random.uniform(1.5, 3.0) * s,
                })

    def _pick_idle(self, lo: float, hi: float) -> None:
        roll = random.random()
        wander_p = 0.10 if self.focus else 0.35
        if roll < 0.45:
            # look around
            self.look_target = [random.uniform(-1, 1), random.uniform(-0.4, 0.6)]
            self.next_look = self.t + random.uniform(1.5, 3.0)
            self.idle_timer = random.uniform(2.0, 5.0)
        elif roll < 0.45 + wander_p:
            # wander a bit
            tgt = self.x + random.choice([-1, 1]) * random.uniform(100, 420)
            tgt = min(max(tgt, lo), hi)
            if abs(tgt - self.x) < 40:
                tgt = min(max(self.x - (tgt - self.x) * 3 - 120, lo), hi)
            self.wander_to, self.behavior = tgt, "wander"
        else:
            # small hop in place
            self.bounce = 0.6
            self.squash_v -= 2.0
            self.tilt_v += random.choice([-1, 1]) * 2.5
            self.idle_timer = random.uniform(3.0, 7.0)

    def _update_head(self, dt: float) -> None:
        s, mood = self.s, self.mood_now()
        target = max(-1.0, min(1.0, self.vx / (170 * s))) * 0.20
        drop = 0.0

        if mood == "listen":
            target += 0.22 if int(self.t / 3) % 2 == 0 else -0.22
        elif mood == "panic":
            target += math.sin(self.t * 32) * 0.10
        elif mood == "happy":
            target += math.sin(self.t * 14) * 0.07
        elif mood == "sad":
            drop, target = 0.10, target + 0.05
        elif mood == "sleepy":
            drop = 0.04 + 0.08 * self.sleep_p
            target = 0.05 + 0.25 * self.sleep_p + math.sin(self.t * 0.8) * 0.04
        elif mood == "love":
            target += math.sin(self.t * 10) * 0.08
        elif mood == "bored":
            target += math.sin(self.t * 0.7) * 0.04

        # typing sway: subtle head bob that follows typing energy
        target += self.typing_energy * math.sin(self.t * 22.0) * 0.05

        acc = 70.0 * (target - self.tilt) - 10.0 * self.tilt_v
        self.tilt_v += acc * dt
        self.tilt += self.tilt_v * dt

        # spring antenna
        target_ant = max(-1.0, min(1.0, self.vx / (170 * s)))
        aa = (target_ant - self.ant) * 220.0 - self.ant_v * 14.0
        self.ant_v += aa * dt
        self.ant += self.ant_v * dt

        self.drop += (drop - self.drop) * min(1.0, dt * 6)

    def _update_look(self, dt: float, env: Env) -> None:
        hx = self.x
        hy = self.y - self.hop_h - 1.9 * self.R
        dx, dy = env.cursor[0] - hx, env.cursor[1] - hy

        if not self.eye_tracking:
            tgt = [0.0, 0.0]
        elif self.mood_now() == "sleepy":
            tgt = [0.0, 0.5]
        elif self.thinking:
            tgt = [0.35, -0.25]
        elif math.hypot(dx, dy) < 700:
            tgt = [
                max(-1.0, min(1.0, dx / 260.0)),
                max(-0.6, min(0.8, dy / 260.0)),
            ]
        else:
            if self.t >= self.next_look:
                self.look_target = [random.uniform(-1, 1), random.uniform(-0.4, 0.6)]
                self.next_look = self.t + random.uniform(1.5, 3.0)
            tgt = self.look_target

        k = min(1.0, dt * 8)
        self.look[0] += (tgt[0] - self.look[0]) * k
        self.look[1] += (tgt[1] - self.look[1]) * k

    # ------------------------------------------------------------------
    # public update
    # ------------------------------------------------------------------
    def update(self, dt: float, env: Env) -> None:
        """Advance the simulation by dt seconds in the given environment."""
        dt = min(max(dt, 0.0), 0.05)
        self.t += dt
        self._dt = dt
        self.work = env.work

        self._update_sleep(dt)
        self._update_particles(dt)
        self._update_bubble()
        self._update_blink(dt)
        self._update_squash(dt)

        self.bounce = max(0.0, self.bounce - dt * 2.2)

        if self.mode == "drag":
            self.vx *= 0.92
        elif self.mode == "hop":
            self._update_hop()
        elif self.mode == "fall":
            self._update_fall(dt, env)
        else:
            self._update_ground(dt, env)

        self._update_head(dt)
        self._update_look(dt, env)

        # thinking dots phase
        if self.thinking:
            self._think_phase = (self._think_phase + dt * 2.0) % 1.0

    # ------------------------------------------------------------------
    # debug / testing helpers
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """Return a plain dict of the sim state, useful for tests + logging."""
        return {
            "t": self.t,
            "x": self.x, "y": self.y,
            "vx": self.vx, "vy": self.vy,
            "mode": self.mode,
            "behavior": self.behavior,
            "mood": self.mood_now(),
            "squash": self.squash,
            "sleep_p": self.sleep_p,
            "thinking": self.thinking,
            "particles": len(self.particles),
            "bubble": (self.bubble or {}).get("text"),
        }


# === END OF PART 1 =========================================================

# ============================================================================
# QT RENDERING HELPERS
# ============================================================================


def _poly(cx: float, cy: float, pts: Iterable[Tuple[float, float]]) -> QPolygonF:
    """Build a QPolygonF translated to (cx, cy) from a list of (x, y) offsets.

    The renderer builds all its geometry in local coordinates (0,0 = a natural
    origin) and passes that through this helper to place it on the widget.
    Using one helper keeps the render methods readable and lets us swap
    coordinate systems (contact point vs. head center vs. bubble anchor)
    without juggling QPointF everywhere.
    """
    return QPolygonF([QPointF(cx + x, cy + y) for x, y in pts])


def _qcolor(rgb: Sequence[int], a: int = 255) -> QColor:
    """Tuple -> QColor with optional alpha."""
    r, g, b = rgb[0], rgb[1], rgb[2]
    return QColor(int(r), int(g), int(b), int(a))


def _blend(rgb_a: Sequence[int], rgb_b: Sequence[int], t: float) -> Tuple[int, int, int]:
    """Linear blend between two RGB triples. t=0 -> a, t=1 -> b."""
    t = max(0.0, min(1.0, t))
    return (
        int(rgb_a[0] + (rgb_b[0] - rgb_a[0]) * t),
        int(rgb_a[1] + (rgb_b[1] - rgb_a[1]) * t),
        int(rgb_a[2] + (rgb_b[2] - rgb_a[2]) * t),
    )


def _lighten(rgb: Sequence[int], amount: float) -> Tuple[int, int, int]:
    return _blend(rgb, (255, 255, 255), amount)


def _darken(rgb: Sequence[int], amount: float) -> Tuple[int, int, int]:
    return _blend(rgb, (0, 0, 0), amount)


# ============================================================================
# TRAY ICON
# ============================================================================


def make_tray_icon(theme_name: str = "classic", size: int = 64,
                   sleeping: bool = False) -> QIcon:
    """Draw a small theme-aware Orbi for the system tray.

    This mirrors the big Orbi's silhouette at 64x64 so the tray icon is
    immediately recognisable. `sleeping=True` closes the eye to give the
    user a visual cue that Orbi is napping.
    """
    theme = THEMES.get(theme_name) or THEMES["classic"]
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    k = size / 64.0

    body_outline = QColor(70, 70, 80, 220)
    body_mid = _qcolor(theme["body_mid"])
    body_lo = _qcolor(theme["body_lo"])
    orange = _qcolor(theme["orange"])
    dark = _qcolor(theme["dark"])
    dome_hi = _qcolor(theme["dome_hi"])

    # --- body (spherical) ------------------------------------------------
    body_grad = QRadialGradient(QPointF(26 * k, 36 * k), 40 * k)
    body_grad.setColorAt(0.0, QColor(255, 255, 255, 220))
    body_grad.setColorAt(0.55, body_mid)
    body_grad.setColorAt(1.0, body_lo)
    p.setPen(QPen(body_outline, max(1.0, 2 * k)))
    p.setBrush(QBrush(body_grad))
    p.drawEllipse(QPointF(32 * k, 43 * k), 20 * k, 20 * k)

    # --- body patches (mimic the sphere patches) -------------------------
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(orange)
    p.drawEllipse(QPointF(37 * k, 46 * k), 8 * k, 8 * k)
    p.setBrush(dark)
    p.drawEllipse(QPointF(37 * k, 46 * k), 4 * k, 4 * k)
    p.setBrush(QColor(255, 255, 255, 90))
    p.drawEllipse(QPointF(24 * k, 36 * k), 3 * k, 3 * k)

    # --- dome ------------------------------------------------------------
    dome_path = QPainterPath()
    dome_path.moveTo(14 * k, 25 * k)
    dome_path.arcTo(QRectF(14 * k, 7 * k, 36 * k, 36 * k), 180, -180)
    dome_path.closeSubpath()

    dome_grad = QRadialGradient(QPointF(24 * k, 12 * k), 30 * k)
    dome_grad.setColorAt(0.0, QColor(255, 255, 255, 240))
    dome_grad.setColorAt(1.0, dome_hi)
    p.setPen(QPen(body_outline, max(1.0, 2 * k)))
    p.setBrush(QBrush(dome_grad))
    p.drawPath(dome_path)

    # --- base ring under the dome ----------------------------------------
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(112, 112, 124, 200))
    p.drawEllipse(QPointF(32 * k, 26 * k), 18.5 * k, 2.4 * k)

    # --- dome orange band ------------------------------------------------
    p.setBrush(orange)
    band = QPainterPath()
    band.moveTo(15 * k, 21 * k)
    band.lineTo(49 * k, 21 * k)
    band.lineTo(49 * k, 24 * k)
    band.lineTo(15 * k, 24 * k)
    band.closeSubpath()
    p.drawPath(band)

    # --- eye -------------------------------------------------------------
    if sleeping:
        p.setPen(QPen(QColor(38, 42, 50), max(1.5, 2.4 * k),
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(31 * k, 16 * k), QPointF(43 * k, 16 * k))
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(38, 42, 50))
        p.drawEllipse(QPointF(37 * k, 16 * k), 7 * k, 7 * k)

        eye_grad = QRadialGradient(QPointF(35 * k, 14 * k), 8 * k)
        eye_grad.setColorAt(0.0, QColor(180, 240, 255))
        eye_grad.setColorAt(1.0, QColor(60, 140, 200))
        p.setBrush(QBrush(eye_grad))
        p.drawEllipse(QPointF(37 * k, 16 * k), 4.5 * k, 4.5 * k)

        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(35.4 * k, 14.4 * k), 1.4 * k, 1.4 * k)

    # --- antenna nub (small, hinting at the 3D shape) --------------------
    p.setPen(QPen(QColor(150, 150, 158), max(1.2, 1.8 * k),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(32 * k, 8 * k), QPointF(31 * k, 3 * k))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(orange)
    p.drawEllipse(QPointF(31 * k, 3 * k), 1.8 * k, 1.8 * k)

    p.end()
    return QIcon(pm)


# ============================================================================
# ORBI WINDOW
# ============================================================================
#
# The window is a transparent, frameless, always-on-top, no-activate Tool
# widget. It has one job: paint the current Sim state. All hit-testing and
# mouse handling is local; the controller wires callbacks for the meaningful
# gestures (right-click -> settings, double-click -> duck).
#
# Rendering order (bottom to top):
#     1. focus ring (only during pomodoro focus)
#     2. ground shadow (two ellipses for depth)
#     3. body sphere (with squash anchored at the contact point)
#     4. head (base ring, dome, band, eyes, antennas)
#     5. particles (confetti, stars, hearts, dust)
#     6. seasonal hat (Halloween / Christmas / New Year)
#     7. ZZZ (sleeping)
#     8. thought dots (thinking)
#     9. reaction glyph (! / ?)
#    10. speech bubble
#    11. time-of-day tint (subtle overlay)
#


class OrbiWindow(QWidget):
    """Frameless, always-on-top, translucent widget that renders Orbi."""

    #: Widget width. Fixed so the speech bubble can overhang the character
    #: without the widget being resized every frame.
    W: int = 380

    #: Vertical space at the top reserved for the speech bubble, reaction
    #: glyph, thought dots, and seasonal hat. Larger = more room above the
    #: head.
    BUBBLE_ZONE: int = 150

    #: Extra bottom margin so the drop shadow isn't clipped by the widget.
    GMARGIN: int = 14

    def __init__(self, sim: Sim):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)

        self.sim = sim
        self.ox: float = 0.0
        self.oy: float = 0.0

        # Callbacks (set by the controller)
        self.settings_cb: Optional[Callable[[], None]] = None
        self.double_cb: Optional[Callable[[], None]] = None

        # Mouse drag state
        self._press: Optional[QPointF] = None
        self._moved: bool = False
        self._grab: Tuple[float, float] = (0.0, 0.0)

        # Theme + seasonal (pushed from the controller on change)
        self.theme_name: str = "classic"
        self.seasonal_enabled: bool = True
        self.seasonal_override: str = ""

        # Small rolling average of frame time, used to smooth the visual
        # breathing animation even if the tick rate is uneven.
        self._last_hit_update: float = 0.0

        self.apply_scale()

    # ------------------------------------------------------------------
    # geometry / sync
    # ------------------------------------------------------------------
    def apply_scale(self) -> None:
        """Recompute widget height from the current sim scale."""
        self.H = int(self.BUBBLE_ZONE + self.sim.total_h + 30)
        self.resize(self.W, self.H)

    def sync(self, work: Tuple[int, int, int, int]) -> None:
        """Move the widget to follow the sim's world position and repaint."""
        l, t, r, b = work
        sim = self.sim
        ox = max(l, min(sim.x - self.W / 2, r - self.W))
        # Anchor the widget so the sim's `y` (Orbi's feet) lands at
        # widget-local `H - GMARGIN`. That keeps the ground line stable
        # even when Orbi hops.
        oy = max(t, sim.y - (self.H - self.GMARGIN))
        self.ox, self.oy = ox, oy
        self.move(int(round(ox)), int(round(oy)))
        self.update()

    def hit_rect(self) -> QRectF:
        """Bounding box of Orbi in widget-local pixels, for mouse hit-testing."""
        R = self.sim.R
        lx = self.sim.x - self.ox
        gy = self.sim.y - self.oy
        lift = self.sim.hop_h + self.sim.bounce_px()
        # 2.4R wide, 3.4R tall above the contact point, plus lift
        return QRectF(
            lx - 1.2 * R,
            gy - lift - 3.4 * R,
            2.4 * R,
            3.4 * R + lift,
        )

    # ------------------------------------------------------------------
    # mouse
    # ------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802 (Qt override)
        pos = e.position()
        if not self.hit_rect().contains(pos):
            return
        if e.button() == Qt.MouseButton.RightButton:
            if self.settings_cb:
                try:
                    self.settings_cb()
                except Exception:
                    logging.exception("settings callback failed")
        elif e.button() == Qt.MouseButton.LeftButton:
            gp = e.globalPosition()
            self._press = gp
            self._moved = False
            self._grab = (self.sim.x - gp.x(), self.sim.y - gp.y())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None:
            return
        gp = e.globalPosition()
        if not self._moved:
            dx = abs(gp.x() - self._press.x())
            dy = abs(gp.y() - self._press.y())
            if dx + dy > 5:
                self._moved = True
                self.sim.begin_drag()
        if self._moved:
            self.sim.drag_to(gp.x() + self._grab[0], gp.y() + self._grab[1])

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._press is not None:
            if self._moved:
                self.sim.end_drag()
            else:
                self.sim.poke()
            self._press = None
            self._moved = False

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and \
                self.hit_rect().contains(e.position()):
            if self.double_cb:
                try:
                    self.double_cb()
                except Exception:
                    logging.exception("double-click callback failed")

    # ------------------------------------------------------------------
    # paint dispatch
    # ------------------------------------------------------------------
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        sim = self.sim
        R = sim.R
        lx = sim.x - self.ox
        gy = sim.y - self.oy
        lift = sim.hop_h + sim.bounce_px()
        breathe = sim.breathing_offset()
        cy = gy - R - lift + breathe

        # 1. focus ring (behind Orbi)
        if sim.focus and not self.sim.asleep if hasattr(self.sim, "asleep") else sim.focus:
            self._draw_focus_ring(p, lx, cy, R)

        # 2. ground shadow
        self._draw_shadow(p, lx, gy, R, lift)

        # 3. body
        self._draw_body(p, lx, cy, R)

        # 4. head + everything attached to it
        head_top, hx, hy, hr = self._draw_head(p, lx, cy, R)

        # 5. particles
        self._draw_particles(p)

        # 6. seasonal hat
        self._draw_seasonal(p, head_top, hx, hy, hr)

        # 7. ZZZ
        if sim.drowsy or sim.sleep_p > 0.1:
            self._draw_zzz(p, lx, head_top, R)

        # 8. thought dots
        if sim.thinking:
            self._draw_thought_dots(p, lx, head_top, R)

        # 9. reaction glyph
        self._draw_reaction(p, lx, head_top, R)

        # 10. speech bubble
        self._draw_bubble(p, lx, head_top)

        # 11. tint
        self._draw_tint(p)

        p.end()

    # ------------------------------------------------------------------
    # shadow
    # ------------------------------------------------------------------
    def _draw_shadow(self, p: QPainter, lx: float, gy: float,
                     R: float, lift: float) -> None:
        """Two-layer contact shadow, sized down as Orbi rises."""
        k = max(0.35, 1.0 - lift / (3 * R))
        p.setPen(Qt.PenStyle.NoPen)
        # Outer soft halo
        p.setBrush(QColor(0, 0, 0, int(45 * k)))
        p.drawEllipse(QPointF(lx, gy + 2), R * 1.10 * k, R * 0.22 * k)
        # Inner denser core
        p.setBrush(QColor(0, 0, 0, int(90 * k)))
        p.drawEllipse(QPointF(lx, gy), R * 0.88 * k, R * 0.14 * k)

    # ------------------------------------------------------------------
    # body
    # ------------------------------------------------------------------
    def _draw_body(self, p: QPainter, cx: float, cy: float, R: float) -> None:
        """Sphere with the theme's body gradient, patches, seams, and band.

        The squash transform is anchored at the contact point (cx, cy + R)
        so the bottom of the sphere stays planted on the ground during a
        landing squash. Without this anchor the sphere appears to lift off
        the surface, which reads as floaty rather than weighty.
        """
        sim = self.sim
        M = sim.orient
        sx, sy = sim.squash_xy()

        p.save()
        if abs(sx - 1.0) > 0.005 or abs(sy - 1.0) > 0.005:
            p.translate(cx, cy + R)
            p.scale(sx, sy)
            p.translate(-cx, -(cy + R))

        # --- base sphere -------------------------------------------------
        g = QRadialGradient(QPointF(cx - 0.35 * R, cy - 0.40 * R), R * 1.55)
        g.setColorAt(0.0, _qcolor(CUR_THEME["body_hi"]))
        g.setColorAt(0.55, _qcolor(CUR_THEME["body_mid"]))
        g.setColorAt(1.0, _qcolor(CUR_THEME["body_lo"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # --- clip to the sphere for patches + seams ----------------------
        clip = QPainterPath()
        clip.addEllipse(QPointF(cx, cy), R * 0.995, R * 0.995)
        p.save()
        p.setClipPath(clip)

        # Patches, back to front, skip anything too far behind
        items = sorted(
            ((mat_vec(M, n)[2], n, layers) for n, layers in BODY_PATCHES),
            key=lambda it: it[0],
        )
        for z, n, layers in items:
            if z < -0.75:
                continue
            for pts, col in layers:
                poly = project_patch(pts, M, R)
                if poly:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_qcolor(col))
                    p.drawPolygon(_poly(cx, cy, poly))

        # Orange band (the widest seam)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_qcolor(ORANGE), 0.10 * R,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for run in visible_runs(BODY_BAND, M, R):
            p.drawPolyline(_poly(cx, cy, run))

        # Darker seams
        p.setPen(QPen(QColor(60, 60, 70, 150), max(1.0, 0.03 * R)))
        for seam in BODY_SEAMS:
            for run in visible_runs(seam, M, R):
                p.drawPolyline(_poly(cx, cy, run))

        # --- shading over everything inside the sphere -------------------
        sh = QRadialGradient(QPointF(cx - 0.28 * R, cy - 0.32 * R), R * 1.35)
        sh.setColorAt(0.0, QColor(255, 255, 255, 0))
        sh.setColorAt(0.62, QColor(0, 0, 0, 0))
        sh.setColorAt(1.0, QColor(20, 20, 40, 110))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # Specular highlight
        hl = QRadialGradient(QPointF(cx - 0.38 * R, cy - 0.45 * R), R * 0.55)
        hl.setColorAt(0.0, QColor(255, 255, 255, 150))
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hl))
        p.drawEllipse(QPointF(cx, cy), R, R)

        p.restore()  # unclip

        # --- outline -----------------------------------------------------
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(80, 78, 90, 200), max(1.0, 0.03 * R)))
        p.drawEllipse(QPointF(cx, cy), R, R)

        p.restore()  # unsquash

    # ------------------------------------------------------------------
    # head
    # ------------------------------------------------------------------
    def _draw_head(self, p: QPainter, cx: float, cy: float, R: float) \
            -> Tuple[float, float, float, float]:
        """Draw the head. Returns (head_top_y, head_cx, head_cy, head_r)."""
        sim = self.sim
        r = 0.56 * R
        hx = cx + sim.look[0] * 0.10 * R
        hy = cy - 0.93 * R + sim.drop * R + math.sin(sim.t * 2.0) * 0.012 * R

        p.save()
        p.translate(hx, hy)
        p.rotate(math.degrees(sim.tilt))
        p.setPen(Qt.PenStyle.NoPen)

        # --- base ring under the dome ------------------------------------
        p.setBrush(QColor(112, 112, 124))
        p.drawEllipse(QPointF(0, 0.06 * r), 1.06 * r, 0.20 * r)

        # --- dome --------------------------------------------------------
        dg = QRadialGradient(QPointF(-0.3 * r, -0.65 * r), 1.7 * r)
        dg.setColorAt(0.0, _qcolor(CUR_THEME["dome_hi"]))
        dg.setColorAt(0.5, _qcolor(CUR_THEME["dome_mid"]))
        dg.setColorAt(1.0, _qcolor(CUR_THEME["dome_lo"]))
        p.setBrush(QBrush(dg))
        p.setPen(QPen(QColor(90, 90, 98), max(1.0, 0.04 * r)))
        p.drawPolygon(_poly(0, 0, dome_outline(r)))

        # --- dome orange band --------------------------------------------
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qcolor(ORANGE))
        p.drawPolygon(_poly(0, 0, dome_band(r, -0.22 * r, -0.09 * r)))

        # --- eyes --------------------------------------------------------
        self._draw_eyes(p, r)

        # --- antennas ----------------------------------------------------
        self._draw_antennas(p, r)

        p.restore()

        # Return head info in *screen* coordinates for the seasonal hats,
        # speech bubble anchor, and reaction glyph. We ignore the rotation
        # for hat placement — it looks fine because hats are small.
        head_top = hy - r * 1.25 - 0.1 * R
        return (head_top, hx, hy, r)

    def _draw_eyes(self, p: QPainter, r: float) -> None:
        """Draw the main lens + small companion eye with mood colors."""
        sim = self.sim
        mood = sim.mood_now()
        col = MOOD_COLORS.get(mood, MOOD_COLORS["normal"])
        if mood == "listen":
            f = 0.8 + 0.2 * math.sin(sim.t * 5)
            col = tuple(int(c * f) for c in col)
        dark = tuple(int(c * 0.45) for c in col)
        bright = tuple(min(255, int(c * 1.1 + 45)) for c in col)

        ex = sim.look[0] * 0.42 * r
        ey = -0.44 * r + sim.look[1] * 0.10 * r
        sq = 1.0 - 0.35 * abs(sim.look[0])

        # Eye socket
        p.setBrush(QColor(38, 42, 50))
        p.setPen(QPen(QColor(150, 150, 160), max(1.0, 0.05 * r)))
        p.drawEllipse(QPointF(ex, ey), 0.36 * r * sq, 0.36 * r)

        # Pupil + iris
        op = sim.eye_open()
        lg = QRadialGradient(QPointF(ex - 0.05 * r, ey - 0.05 * r), 0.3 * r)
        lg.setColorAt(0.0, _qcolor(bright))
        lg.setColorAt(1.0, _qcolor(dark))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(lg))
        p.drawEllipse(QPointF(ex, ey), 0.27 * r * sq, 0.27 * r * max(0.06, op))

        # Highlight
        if op > 0.4:
            p.setBrush(QColor(255, 255, 255, 210))
            p.drawEllipse(
                QPointF(ex - 0.09 * r * sq, ey - 0.09 * r),
                0.06 * r, 0.06 * r,
            )

        # Small companion eye
        sx = ex - 0.52 * r * sq
        sy = ey + 0.32 * r
        p.setBrush(QColor(38, 42, 50))
        p.drawEllipse(QPointF(sx, sy), 0.12 * r, 0.12 * r)
        p.setBrush(_qcolor(dark if op < 0.3 else col))
        p.drawEllipse(QPointF(sx, sy), 0.07 * r, 0.07 * r)

    def _draw_antennas(self, p: QPainter, r: float) -> None:
        """Two curved antennas with orange tips. First one is longer."""
        sim = self.sim
        sway = sim.ant_value() * 0.35 * r
        antenna_pen = QPen(
            QColor(150, 150, 158),
            max(1.2, 0.07 * r),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
        p.setBrush(Qt.BrushStyle.NoBrush)

        for ax, length in ((0.28, 1.15), (-0.06, 0.55)):
            x0 = ax * r
            y0 = -math.sqrt(max(0.0, r * r - x0 * x0)) + 0.03 * r
            tx = x0 - sway * (length / 1.15)
            ty = y0 - length * r

            path = QPainterPath()
            path.moveTo(x0, y0)
            path.quadTo(
                x0 - sway * 0.2,
                y0 - length * r * 0.55,
                tx, ty,
            )
            p.setPen(antenna_pen)
            p.drawPath(path)

            # Tip
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_qcolor(ORANGE))
            p.drawEllipse(QPointF(tx, ty), 0.07 * r, 0.07 * r)

            # Restore pen for next antenna
            p.setPen(antenna_pen)

    # ------------------------------------------------------------------
    # particles
    # ------------------------------------------------------------------
    def _draw_particles(self, p: QPainter) -> None:
        for q in self.sim.particles:
            life = q.get("life", 0.0)
            l0 = q.get("l0", 1.0) or 1.0
            a = max(0.0, min(1.0, life / l0))
            if a <= 0.01:
                continue
            px = q["x"] - self.ox
            py = q["y"] - self.oy
            kind = q.get("k", "c")
            if kind == "c":
                self._draw_confetti(p, q, px, py, a)
            elif kind == "s":
                self._draw_star(p, q, px, py, a)
            elif kind == "h":
                self._draw_heart(p, q, px, py, a)
            else:
                self._draw_dust(p, q, px, py, a)

    def _draw_confetti(self, p: QPainter, q: Dict[str, Any],
                       px: float, py: float, a: float) -> None:
        p.save()
        p.setOpacity(a)
        p.translate(px, py)
        p.rotate(math.degrees(q.get("r", 0.0)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qcolor(q.get("c", (255, 255, 255))))
        p.drawRect(QRectF(-3, -2, 6, 4))
        p.restore()

    def _draw_star(self, p: QPainter, q: Dict[str, Any],
                   px: float, py: float, a: float) -> None:
        p.save()
        p.setOpacity(a)
        p.translate(px, py)
        p.rotate(math.degrees(q.get("r", 0.0)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 220, 90))
        size = q.get("size", 6.0)
        p.drawPolygon(_poly(0, 0, star_points(size, 5, 0.42)))
        # Small inner highlight
        p.setBrush(QColor(255, 255, 200, 200))
        p.drawPolygon(_poly(0, 0, star_points(size * 0.45, 5, 0.42)))
        p.restore()

    def _draw_heart(self, p: QPainter, q: Dict[str, Any],
                    px: float, py: float, a: float) -> None:
        p.save()
        p.setOpacity(a)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 90, 130))
        p.drawPolygon(_poly(px, py, heart_points(11 * max(0.8, self.sim.s))))
        # Inner lighter heart
        p.setBrush(QColor(255, 160, 190, 200))
        p.drawPolygon(_poly(px, py, heart_points(6 * max(0.8, self.sim.s))))
        p.restore()

    def _draw_dust(self, p: QPainter, q: Dict[str, Any],
                   px: float, py: float, a: float) -> None:
        p.save()
        p.setOpacity(a * 0.9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(215, 215, 225, 190))
        rr = q.get("r", 2.0)
        p.drawEllipse(QPointF(px, py), rr, rr)
        p.restore()

    # ------------------------------------------------------------------
    # ZZZ (sleeping)
    # ------------------------------------------------------------------
    def _draw_zzz(self, p: QPainter, lx: float, top: float, R: float) -> None:
        t = self.sim.t
        for i in range(3):
            ph = (t * 0.5 + i / 3.0) % 1.0
            f = QFont("Segoe UI")
            f.setBold(True)
            f.setPointSizeF(8 + i * 2.5 + ph * 3)
            p.setFont(f)
            p.setPen(QColor(90, 110, 170, int(255 * (1 - ph))))
            p.drawText(
                QPointF(lx + 0.5 * R + ph * 16 + i * 5, top + 0.4 * R - ph * 26),
                "z" if i == 0 else "Z",
            )

    # ------------------------------------------------------------------
    # thought dots
    # ------------------------------------------------------------------
    def _draw_thought_dots(self, p: QPainter, lx: float, top: float, R: float) -> None:
        """Three ascending dots that pulse, like a comic-strip thought."""
        phase = getattr(self.sim, "_think_phase", 0.0)
        base_x = lx + 0.6 * R
        base_y = top - 0.2 * R
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            local = (phase + i / 3.0) % 1.0
            # alpha pulses with a phase offset per dot
            a = int(140 + 100 * math.sin(local * math.tau))
            a = max(40, min(240, a))
            size = (2.0 + i * 0.9 + 0.6 * math.sin(local * math.tau)) * self.sim.s
            cx = base_x + i * (4.5 * self.sim.s) + math.sin(local * math.tau) * 1.6
            cy = base_y - i * (7.0 * self.sim.s)
            p.setBrush(QColor(230, 235, 250, a))
            p.drawEllipse(QPointF(cx, cy), size, size)

    # ------------------------------------------------------------------
    # reaction glyph (! or ?)
    # ------------------------------------------------------------------
    def _draw_reaction(self, p: QPainter, lx: float, top: float, R: float) -> None:
        """A quick '!' or '?' that pops above the head."""
        state = self.sim.reaction_state()
        if state is None:
            return
        glyph, alpha, scale = state
        if alpha <= 0.02:
            return

        # Position: slightly to the side of the head top
        cx = lx + 0.55 * R
        cy = top - 0.25 * R

        p.save()
        p.setOpacity(alpha)
        p.translate(cx, cy)
        p.scale(scale, scale)

        # Circle background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 235, 90, 230))
        p.drawEllipse(QPointF(0, 0), 12 * self.sim.s, 12 * self.sim.s)
        p.setBrush(QColor(255, 210, 30, 255))
        p.drawEllipse(QPointF(0, 0), 10 * self.sim.s, 10 * self.sim.s)
        p.setPen(QPen(QColor(120, 80, 20, 200), max(1.0, 1.4 * self.sim.s)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0, 0), 12 * self.sim.s, 12 * self.sim.s)

        # Glyph
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPointSizeF(12 * self.sim.s)
        p.setFont(f)
        p.setPen(QColor(70, 40, 10))
        flags = _iv(Qt.AlignmentFlag.AlignCenter)
        p.drawText(QRectF(-14, -14, 28, 28), flags, glyph)

        p.restore()

    # ------------------------------------------------------------------
    # speech bubble
    # ------------------------------------------------------------------
    def _draw_bubble(self, p: QPainter, lx: float, top_y: float) -> None:
        sim = self.sim
        b = sim.bubble
        a = sim.bubble_alpha()
        if not b or a <= 0.01:
            return

        font = QFont("Segoe UI")
        # scale font size with the sim scale for readability at all sizes
        font.setPointSizeF(max(8.5, 9.5 * self.sim.s))
        p.setFont(font)
        fm = QFontMetrics(font)

        flags = (
            _iv(Qt.TextFlag.TextWordWrap)
            | _iv(Qt.AlignmentFlag.AlignLeft)
            | _iv(Qt.AlignmentFlag.AlignTop)
        )
        text = b["text"]
        max_w = int(240 * max(1.0, self.sim.s))
        rect = fm.boundingRect(QRect(0, 0, max_w, 2000), flags, text)
        w = min(max_w, rect.width()) + 22
        h = rect.height() + 18

        bx = max(6.0, min(lx - w / 2, self.W - 6 - w))
        by = max(4.0, top_y - h - 12)
        tx = max(bx + 16, min(lx, bx + w - 16))

        p.save()
        p.setOpacity(a)

        # Bubble body
        p.setPen(QPen(QColor(60, 62, 76), 1.4))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawRoundedRect(QRectF(bx, by, w, h), 11, 11)

        # Tail
        p.setPen(Qt.PenStyle.NoPen)
        tail = QPolygonF([
            QPointF(tx - 8, by + h - 1),
            QPointF(tx + 8, by + h - 1),
            QPointF(tx, by + h + 11),
        ])
        p.drawPolygon(tail)

        # Tail outline (only the two visible sides)
        p.setPen(QPen(QColor(60, 62, 76), 1.4))
        p.drawLine(QPointF(tx - 8, by + h), QPointF(tx, by + h + 11))
        p.drawLine(QPointF(tx + 8, by + h), QPointF(tx, by + h + 11))

        # Text
        p.setPen(QColor(35, 38, 52))
        p.drawText(QRectF(bx + 11, by + 9, w - 22, h - 18), flags, text)

        p.restore()

    # ------------------------------------------------------------------
    # seasonal hats
    # ------------------------------------------------------------------
    def _draw_seasonal(self, p: QPainter, head_top: float,
                       hx: float, hy: float, hr: float) -> None:
        if not self.seasonal_enabled:
            return
        season = self.seasonal_override or current_season()
        if not season or season == "none":
            return

        # Save and translate to head center for convenience
        p.save()
        p.translate(hx, hy)

        if season == "halloween":
            self._draw_pumpkin(p, hr)
        elif season == "christmas":
            self._draw_santa(p, hr)
        elif season == "newyear":
            self._draw_party_hat(p, hr)

        p.restore()

    def _draw_pumpkin(self, p: QPainter, r: float) -> None:
        """A small pumpkin sitting on top of the dome."""
        cx = 0.0
        cy = -r * 0.98
        pw = r * 0.55
        ph = r * 0.40

        # Shadow under the pumpkin
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QPointF(cx, cy + ph * 0.65), pw * 0.9, ph * 0.22)

        # Pumpkin body — 3 overlapping ellipses give the ridged look
        p.setBrush(QColor(235, 130, 40))
        p.drawEllipse(QPointF(cx, cy), pw, ph)
        p.setBrush(QColor(210, 105, 25))
        p.drawEllipse(QPointF(cx - pw * 0.35, cy), pw * 0.65, ph * 0.95)
        p.setBrush(QColor(255, 160, 60))
        p.drawEllipse(QPointF(cx + pw * 0.35, cy), pw * 0.65, ph * 0.95)
        p.setBrush(QColor(235, 130, 40))
        p.drawEllipse(QPointF(cx, cy), pw * 0.9, ph)

        # Vertical ridge lines
        p.setPen(QPen(QColor(180, 80, 15, 180), max(1.0, r * 0.02)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - pw * 0.5, cy - ph * 0.9, pw, ph * 1.8), 90 * 16, 180 * 16)
        p.drawArc(QRectF(cx - pw * 0.1, cy - ph * 0.9, pw * 0.2, ph * 1.8), 90 * 16, 180 * 16)
        p.drawArc(QRectF(cx + pw * 0.4, cy - ph * 0.9, pw * 0.2, ph * 1.8), 90 * 16, 180 * 16)

        # Stem
        p.setPen(QPen(QColor(80, 55, 20), max(1.4, r * 0.05),
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - pw * 0.05, cy - ph * 0.9),
                   QPointF(cx + pw * 0.10, cy - ph * 1.35))

        # Small leaf
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(90, 160, 60))
        leaf = QPainterPath()
        leaf.moveTo(cx + pw * 0.15, cy - ph * 1.15)
        leaf.quadTo(cx + pw * 0.55, cy - ph * 1.40,
                    cx + pw * 0.65, cy - ph * 1.05)
        leaf.quadTo(cx + pw * 0.40, cy - ph * 1.05,
                    cx + pw * 0.15, cy - ph * 1.15)
        p.drawPath(leaf)

    def _draw_santa(self, p: QPainter, r: float) -> None:
        """A red santa hat leaning to the right."""
        base_x = -r * 0.05
        base_y = -r * 0.95
        base_w = r * 1.20
        tip_x = r * 0.85
        tip_y = -r * 2.10

        # Cone body
        path = QPainterPath()
        path.moveTo(base_x - base_w * 0.5, base_y)
        path.quadTo(
            base_x + (tip_x - base_x) * 0.2,
            base_y - r * 0.55,
            tip_x, tip_y,
        )
        path.quadTo(
            base_x + (tip_x - base_x) * 0.55,
            base_y - r * 0.60,
            base_x + base_w * 0.5, base_y,
        )
        path.closeSubpath()

        grad = QLinearGradient(QPointF(base_x, base_y), QPointF(tip_x, tip_y))
        grad.setColorAt(0.0, QColor(200, 20, 30))
        grad.setColorAt(0.7, QColor(230, 40, 50))
        grad.setColorAt(1.0, QColor(180, 15, 25))
        p.setPen(QPen(QColor(120, 10, 15), max(1.0, r * 0.03)))
        p.setBrush(QBrush(grad))
        p.drawPath(path)

        # White trim band at the base
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(250, 250, 250))
        band = QPainterPath()
        band.moveTo(base_x - base_w * 0.55, base_y)
        band.quadTo(base_x, base_y - r * 0.22,
                    base_x + base_w * 0.55, base_y)
        band.quadTo(base_x, base_y + r * 0.14,
                    base_x - base_w * 0.55, base_y)
        band.closeSubpath()
        p.drawPath(band)
        p.setPen(QPen(QColor(210, 210, 215), max(1.0, r * 0.02)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(band)

        # Pom-pom
        p.setPen(Qt.PenStyle.NoPen)
        pom_r = r * 0.22
        p.setBrush(QColor(250, 250, 250))
        p.drawEllipse(QPointF(tip_x, tip_y), pom_r, pom_r)
        p.setBrush(QColor(230, 230, 235, 180))
        p.drawEllipse(QPointF(tip_x + pom_r * 0.25, tip_y + pom_r * 0.25),
                      pom_r * 0.55, pom_r * 0.55)

    def _draw_party_hat(self, p: QPainter, r: float) -> None:
        """A cone party hat with stripes and a star on top."""
        cx = 0.0
        base_y = -r * 0.95
        base_w = r * 1.00
        tip_y = -r * 1.95

        # Cone
        path = QPainterPath()
        path.moveTo(cx - base_w * 0.5, base_y)
        path.lineTo(cx + base_w * 0.5, base_y)
        path.lineTo(cx, tip_y)
        path.closeSubpath()

        grad = QLinearGradient(QPointF(cx, base_y), QPointF(cx, tip_y))
        grad.setColorAt(0.0, QColor(100, 180, 255))
        grad.setColorAt(1.0, QColor(40, 100, 220))
        p.setPen(QPen(QColor(30, 60, 140), max(1.0, r * 0.03)))
        p.setBrush(QBrush(grad))
        p.drawPath(path)

        # Diagonal stripes (clip to the cone first)
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        stripe_w = r * 0.28
        stripe_color = QColor(255, 245, 100, 220)
        y = base_y
        while y > tip_y - stripe_w:
            poly = QPolygonF([
                QPointF(cx - base_w, y),
                QPointF(cx + base_w, y),
                QPointF(cx + base_w, y - stripe_w * 0.5),
                QPointF(cx - base_w, y - stripe_w * 0.5),
            ])
            p.setBrush(stripe_color)
            p.drawPolygon(poly)
            y -= stripe_w * 1.6
        p.restore()

        # Star on top
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 220, 60))
        p.drawPolygon(_poly(cx, tip_y - r * 0.10, star_points(r * 0.28, 5, 0.45)))
        p.setBrush(QColor(255, 255, 200, 200))
        p.drawPolygon(_poly(cx, tip_y - r * 0.10, star_points(r * 0.15, 5, 0.45)))

    # ------------------------------------------------------------------
    # time-of-day tint
    # ------------------------------------------------------------------
    def _draw_tint(self, p: QPainter) -> None:
        """Time-of-day color wash, clipped to Orbi's body+head silhouette."""
        r, g, b, a = time_of_day_tint()
        if a <= 0:
            return

        sim = self.sim
        R = sim.R
        lx = sim.x - self.ox
        gy = sim.y - self.oy
        lift = sim.hop_h + sim.bounce_px()
        cy = gy - R - lift + sim.breathing_offset()

        # Body silhouette
        body = QPainterPath()
        body.addEllipse(QPointF(lx, cy), R, R)

        # Head silhouette
        hx = lx + sim.look[0] * 0.10 * R
        hy = cy - 0.93 * R + sim.drop * R
        hr = 0.56 * R
        head = QPainterPath()
        head.addEllipse(QPointF(hx, hy), hr * 1.05, hr * 1.05)

        # Union and use as clip region
        clip = body.united(head)
        p.save()
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(r, g, b, a))
        p.drawRect(self.rect())
        p.restore()

    # ------------------------------------------------------------------
    # focus mode ring (pomodoro)
    # ------------------------------------------------------------------
    def _draw_focus_ring(self, p: QPainter, lx: float, cy: float, R: float) -> None:
        """A faint ring around Orbi while the focus timer is running."""
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Animated pulse
        pulse = 0.5 + 0.5 * math.sin(self.sim.t * 1.4)
        ring_alpha = int(40 + 40 * pulse)
        pen = QPen(QColor(120, 200, 255, ring_alpha), max(1.2, R * 0.06))
        p.setPen(pen)
        ring_r = R * 1.35
        p.drawEllipse(QPointF(lx, cy), ring_r, ring_r * 0.55)
        # Inner brighter arc
        pen2 = QPen(QColor(180, 230, 255, min(220, ring_alpha + 60)),
                    max(1.0, R * 0.035))
        p.setPen(pen2)
        p.drawArc(
            QRectF(lx - ring_r, cy - ring_r * 0.55, ring_r * 2, ring_r * 1.1),
            int(self.sim.t * 60) * 16,
            60 * 16,
        )
        p.restore()


# === END OF PART 2 ==========================================================

# ============================================================================
# DIALOG STYLESHEET
# ============================================================================
#
# One stylesheet used by every dialog. Colors derive from the current theme
# so dialogs feel like a natural extension of Orbi rather than a generic
# Qt widget. Applied in each dialog's __init__.

def dialog_qss(theme: Optional[Dict[str, Any]] = None) -> str:
    t = theme or CUR_THEME
    bg = t.get("bg", (31, 36, 48))
    fg = t.get("fg", (230, 233, 242))
    accent = t.get("orange", (238, 118, 34))

    def rgb(c):  # tuple -> css color string
        return "rgb(%d, %d, %d)" % (c[0], c[1], c[2])

    bg_s = rgb(bg)
    fg_s = rgb(fg)
    accent_s = rgb(accent)
    panel = rgb(tuple(min(255, c + 14) for c in bg))
    edge = rgb(tuple(min(255, c + 34) for c in bg))
    edge_hi = rgb(tuple(min(255, c + 60) for c in bg))

    return f"""
    QDialog {{ background: {bg_s}; color: {fg_s}; }}
    QLabel {{ color: {fg_s}; font-size: 12px; }}
    QGroupBox {{
        color: {fg_s};
        border: 1px solid {edge};
        border-radius: 6px;
        margin-top: 14px;
        padding-top: 12px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 12px; padding: 0 4px;
        color: {accent_s};
    }}
    QCheckBox {{ color: {fg_s}; padding: 4px; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {edge_hi}; border-radius: 3px;
        background: {panel};
    }}
    QCheckBox::indicator:checked {{
        background: {accent_s}; border-color: {accent_s};
    }}
    QComboBox, QSpinBox, QLineEdit {{
        background: {panel};
        color: {fg_s};
        border: 1px solid {edge_hi};
        border-radius: 5px;
        padding: 4px 8px;
        min-height: 20px;
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background: {panel};
        color: {fg_s};
        selection-background-color: {accent_s};
    }}
    QPushButton {{
        background: {panel};
        color: {fg_s};
        border: 1px solid {edge_hi};
        border-radius: 5px;
        padding: 6px 14px;
        min-height: 20px;
    }}
    QPushButton:hover {{ background: {edge_hi}; }}
    QPushButton:pressed {{ background: {edge}; }}
    QPushButton:disabled {{ color: rgba(128,128,128,140); }}
    QTabWidget::pane {{
        border: 1px solid {edge};
        border-radius: 6px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: {panel};
        color: {fg_s};
        padding: 7px 14px;
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {edge_hi};
        color: {accent_s};
    }}
    QListWidget {{
        background: {panel};
        color: {fg_s};
        border: 1px solid {edge_hi};
        border-radius: 5px;
    }}
    QListWidget::item:selected {{
        background: {accent_s};
        color: {bg_s};
    }}
    QTextEdit {{
        background: {panel};
        color: {fg_s};
        border: 1px solid {edge_hi};
        border-radius: 5px;
    }}
    QProgressBar {{
        background: {panel};
        border: 1px solid {edge_hi};
        border-radius: 5px;
        text-align: center;
        color: {fg_s};
    }}
    QProgressBar::chunk {{
        background: {accent_s};
        border-radius: 4px;
    }}
    QScrollArea {{ background: {bg_s}; border: none; }}
    QSlider::groove:horizontal {{
        background: {edge};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {accent_s};
        width: 14px; margin: -6px 0; border-radius: 7px;
    }}
    QMenu {{
        background: {panel};
        color: {fg_s};
        border: 1px solid {edge_hi};
        padding: 4px;
    }}
    QMenu::item {{ padding: 6px 22px 6px 22px; border-radius: 3px; }}
    QMenu::item:selected {{ background: {accent_s}; color: {bg_s}; }}
    QMenu::separator {{ height: 1px; background: {edge}; margin: 4px 8px; }}
    """


# ============================================================================
# ACHIEVEMENTS
# ============================================================================

@dataclass
class Achievement:
    key: str
    title: str
    description: str
    # threshold is compared against the counter named `counter`
    counter: str
    threshold: int
    # optional second condition: (counter, threshold, mode) where mode is
    # "gte" (>=) or "oneof" (counter in threshold-set)
    hidden: bool = False


ACHIEVEMENTS: List[Achievement] = [
    Achievement("first_steps", "First Steps",
                "Orbi rolled off the production line.",
                "launches", 1),
    Achievement("hydrated", "Hydrated Human",
                "Drink water 10 times.",
                "waters_drunk", 10),
    Achievement("stretch_master", "Stretch Master",
                "Stretch 25 times.",
                "stretches", 25),
    Achievement("eye_care", "20/20/20",
                "Rest your eyes 50 times.",
                "eye_rests", 50),
    Achievement("focused", "Deep Focus",
                "Complete 25 pomodoros.",
                "pomodoros", 25),
    Achievement("zen_master", "Zen Master",
                "Complete 100 pomodoros.",
                "pomodoros", 100),
    Achievement("first_pass", "Green Day",
                "First passing test run.",
                "pass_runs", 1),
    Achievement("green_streak", "Green Streak",
                "50 passing test runs.",
                "pass_runs", 50),
    Achievement("bug_hunter", "Bug Hunter",
                "50 failing test runs. Every bug is a lesson.",
                "fail_runs", 50),
    Achievement("commit_50", "Prolific Committer",
                "Orbi celebrated 50 of your commits.",
                "commits", 50),
    Achievement("commit_500", "Shipping Machine",
                "Orbi celebrated 500 of your commits.",
                "commits", 500),
    Achievement("duck_debugger", "Rubber Duck's Best Friend",
                "Have 25 duck conversations.",
                "duck_sessions", 25),
    Achievement("eureka", "Eureka!",
                "Solve a problem out loud.",
                "eurekas", 1),
    Achievement("eureka_10", "Duck Whisperer",
                "Solve 10 problems out loud.",
                "eurekas", 10),
    Achievement("night_owl", "Night Owl",
                "Use Orbi past midnight.",
                "night_uses", 1),
    Achievement("early_bird", "Early Bird",
                "Use Orbi before 6 AM.",
                "early_uses", 1),
    Achievement("long_haul", "Long Haul",
                "Have Orbi running for 24 hours in one session.",
                "long_sessions", 1),
    Achievement("week_streak", "Week of Focus",
                "Use Orbi 7 days in a row.",
                "day_streak", 7),
    Achievement("month_streak", "Month of Focus",
                "Use Orbi 30 days in a row.",
                "day_streak", 30),
    Achievement("poked_100", "Personal Space",
                "Boop Orbi 100 times. He likes it really.",
                "pokes", 100),
    Achievement("builder", "Builder",
                "Run 10 builds.",
                "builds", 10),
    Achievement("nervous_build", "Suspiciously Slow Build",
                "Watch a build take longer than 5 minutes.",
                "nervous_builds", 1),
    Achievement("theme_hopper", "Interior Decorator",
                "Try 5 different themes.",
                "themes_used", 5),
    Achievement("night_mode", "Sleepy Robot",
                "Put Orbi to sleep 10 times.",
                "naps", 10),
]


class Achievements:
    """Persistent unlockable achievements. Plain dict, JSON-backed."""

    def __init__(self, on_unlock: Optional[Callable[[Achievement], None]] = None):
        self.data = load_achievements()
        self.data.setdefault("unlocked", [])
        self.data.setdefault("counters", {})
        self.on_unlock = on_unlock

    @property
    def unlocked(self) -> List[str]:
        return list(self.data.get("unlocked") or [])

    def is_unlocked(self, key: str) -> bool:
        return key in (self.data.get("unlocked") or [])

    def counter(self, name: str) -> int:
        return int(self.data.get("counters", {}).get(name, 0))

    def bump(self, name: str, amount: int = 1) -> None:
        counters = self.data.setdefault("counters", {})
        counters[name] = int(counters.get(name, 0)) + amount
        self._check_unlocks()
        save_achievements(self.data)

    def set_counter(self, name: str, value: int) -> None:
        counters = self.data.setdefault("counters", {})
        counters[name] = int(value)
        self._check_unlocks()
        save_achievements(self.data)

    def _check_unlocks(self) -> None:
        counters = self.data.setdefault("counters", {})
        unlocked = self.data.setdefault("unlocked", [])
        for a in ACHIEVEMENTS:
            if a.key in unlocked:
                continue
            if int(counters.get(a.counter, 0)) >= a.threshold:
                unlocked.append(a.key)
                if self.on_unlock:
                    try:
                        self.on_unlock(a)
                    except Exception:
                        logging.exception("on_unlock failed for %s", a.key)

    def total(self) -> int:
        return len(ACHIEVEMENTS)

    def unlocked_count(self) -> int:
        return len([a for a in ACHIEVEMENTS if a.key in self.unlocked])

    def reset(self) -> None:
        self.data = {"unlocked": [], "counters": {}}
        save_achievements(self.data)


# ============================================================================
# STATS
# ============================================================================

class Stats:
    """Rolling statistics for the Stats dialog. Persisted as JSON.

    Structure:
        {
          "first_seen": iso-timestamp,
          "last_seen": iso-timestamp,
          "sessions": [ {"start": iso, "end": iso, "seconds": int}, ... ],
          "days_used": ["2026-10-16", ...],
          "top_apps": {"exe_name": seconds, ...},
          "pomodoros_today": int,
          "pomodoros_total": int,
          "today_date": "2026-10-16",
        }
    """

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self.data = load_stats()
        self.data.setdefault("first_seen", datetime.now().isoformat(timespec="seconds"))
        self.data.setdefault("last_seen", datetime.now().isoformat(timespec="seconds"))
        self.data.setdefault("sessions", [])
        self.data.setdefault("days_used", [])
        self.data.setdefault("top_apps", {})
        self.data.setdefault("pomodoros_today", 0)
        self.data.setdefault("pomodoros_total", 0)
        self.data.setdefault("today_date", datetime.now().strftime("%Y-%m-%d"))
        self._session_start = time.time()
        self._last_save = 0.0

        self._mark_today()
        self._flush()

    def _mark_today(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        days = self.data.setdefault("days_used", [])
        if today not in days:
            days.append(today)
            # trim to 90 entries
            if len(days) > 90:
                self.data["days_used"] = days[-90:]

    def note_foreground(self, exe: str, seconds: float = 0.4) -> None:
        if not self.enabled or not exe:
            return
        top = self.data.setdefault("top_apps", {})
        top[exe] = float(top.get(exe, 0)) + seconds

    def bump_pomodoro(self) -> None:
        if not self.enabled:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if self.data.get("today_date") != today:
            self.data["today_date"] = today
            self.data["pomodoros_today"] = 0
        self.data["pomodoros_today"] = int(self.data.get("pomodoros_today", 0)) + 1
        self.data["pomodoros_total"] = int(self.data.get("pomodoros_total", 0)) + 1

    def day_streak(self) -> int:
        days = sorted(set(self.data.get("days_used", [])))
        if not days:
            return 0
        today = datetime.now().date()
        # Walk backwards from today, counting consecutive entries.
        streak = 0
        cur = today
        day_set = set(days)
        while cur.isoformat() in day_set:
            streak += 1
            cur = cur - timedelta(days=1)
        return streak

    def session_seconds(self) -> int:
        return int(time.time() - self._session_start)

    def top_apps(self, limit: int = 8) -> List[Tuple[str, float]]:
        items = list((self.data.get("top_apps") or {}).items())
        items.sort(key=lambda kv: kv[1], reverse=True)
        return items[:limit]

    def _flush(self) -> None:
        if not self.enabled:
            return
        self.data["last_seen"] = datetime.now().isoformat(timespec="seconds")
        self.data["sessions"] = self.data.get("sessions", [])[-50:]
        save_stats(self.data)
        self._last_save = time.time()

    def save_periodic(self) -> None:
        if time.time() - self._last_save < 30:
            return
        self._flush()

    def close(self) -> None:
        if not self.enabled:
            return
        secs = self.session_seconds()
        self.data["sessions"].append({
            "start": datetime.fromtimestamp(self._session_start).isoformat(timespec="seconds"),
            "end": datetime.now().isoformat(timespec="seconds"),
            "seconds": secs,
        })
        self._flush()


# ============================================================================
# PLUGIN LOADER
# ============================================================================
#
# A plugin is a .py file in %APPDATA%\\Orbi\\plugins\\ exposing:
#
#     PLUGIN_NAME    = "My plugin"
#     PLUGIN_VERSION = "1.0"
#     PLUGIN_AUTHOR  = "You"             # optional
#
#     def poll(app) -> Optional[str]:    # called on a timer
#         ...                            # return a message to say, or None
#
#     def on_command(cmd: str, arg: str, app) -> bool:   # optional
#         ...                            # return True if you handled it
#
# The `app` argument is the OrbiApp instance. Plugins run in the main
# thread, so they must not block; use threads or QTimers if you need
# longer work.

class PluginLoader:
    """Load and poll user plugins from %APPDATA%\\Orbi\\plugins."""

    def __init__(self, app, enabled: bool = False):
        self.app = app
        self.enabled = bool(enabled)
        self.plugins: List[Any] = []
        self._errors: List[str] = []
        if self.enabled:
            self.load()

    def load(self) -> None:
        self.plugins = []
        self._errors = []
        try:
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                mod = self._load_file(path)
                if hasattr(mod, "poll") or hasattr(mod, "on_command"):
                    self.plugins.append(mod)
                    logging.info("plugin loaded: %s", path.name)
            except Exception as exc:
                msg = "%s: %s" % (path.name, exc)
                self._errors.append(msg)
                logging.exception("plugin load failed: %s", path.name)

    @staticmethod
    def _load_file(path: Path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("orbi_plugin_" + path.stem, str(path))
        if spec is None or spec.loader is None:
            raise ImportError("no loader for %s" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def poll(self) -> None:
        if not self.enabled:
            return
        for mod in self.plugins:
            fn = getattr(mod, "poll", None)
            if fn is None:
                continue
            try:
                result = fn(self.app)
                if isinstance(result, str) and result:
                    self.app.notify(result)
            except Exception:
                logging.exception("plugin poll failed: %s",
                                  getattr(mod, "__name__", "?"))

    def dispatch(self, cmd: str, arg: str) -> bool:
        if not self.enabled:
            return False
        for mod in self.plugins:
            fn = getattr(mod, "on_command", None)
            if fn is None:
                continue
            try:
                if fn(cmd, arg, self.app):
                    return True
            except Exception:
                logging.exception("plugin command failed: %s",
                                  getattr(mod, "__name__", "?"))
        return False

    def errors(self) -> List[str]:
        return list(self._errors)


# ============================================================================
# SETTINGS DIALOG
# ============================================================================

class SettingsDialog(QDialog):
    """Tabbed settings. Every change applies live and is persisted."""

    def __init__(self, app):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.app = app
        self.setWindowTitle("Orbi Settings")
        self.setMinimumSize(520, 480)
        self.setStyleSheet(dialog_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        tabs.addTab(self._tab_appearance(), "Appearance")
        tabs.addTab(self._tab_behaviour(), "Behaviour")
        tabs.addTab(self._tab_reminders(), "Reminders")
        tabs.addTab(self._tab_focus(), "Focus")
        tabs.addTab(self._tab_git(), "Git")
        tabs.addTab(self._tab_system(), "System")
        tabs.addTab(self._tab_about(), "About")

        # bottom row
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        quit_btn = QPushButton("Quit Orbi")
        quit_btn.clicked.connect(self.app.quit)
        bottom.addWidget(quit_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------
    def _tab_appearance(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 16, 12, 12)

        # Theme
        self.theme_combo = QComboBox()
        for key, t in THEMES.items():
            self.theme_combo.addItem(t["label"], key)
        idx = self.theme_combo.findData(self.app.cfg.get("theme", "classic"))
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentIndexChanged.connect(
            lambda i: self.app.set_theme(self.theme_combo.itemData(i))
        )
        form.addRow("Theme", self.theme_combo)

        # Size
        self.size_combo = QComboBox()
        for name, v in (("Small", 0.7), ("Medium", 1.0), ("Large", 1.4)):
            self.size_combo.addItem(name, v)
        cur = float(self.app.cfg.get("scale", 1.0))
        for i in range(self.size_combo.count()):
            if abs(self.size_combo.itemData(i) - cur) < 0.05:
                self.size_combo.setCurrentIndex(i)
                break
        self.size_combo.currentIndexChanged.connect(
            lambda i: self.app.set_scale(self.size_combo.itemData(i))
        )
        form.addRow("Size", self.size_combo)

        # Seasonal
        self.seasonal_cb = QCheckBox("Auto-decorate for holidays")
        self.seasonal_cb.setChecked(bool(self.app.cfg.get("seasonal", True)))
        self.seasonal_cb.toggled.connect(
            lambda v: self.app.set_cfg("seasonal", bool(v))
        )
        form.addRow("", self.seasonal_cb)

        self.seasonal_combo = QComboBox()
        self.seasonal_combo.addItem("Auto", "")
        self.seasonal_combo.addItem("Halloween", "halloween")
        self.seasonal_combo.addItem("Christmas", "christmas")
        self.seasonal_combo.addItem("New Year", "newyear")
        self.seasonal_combo.addItem("None", "none")
        ov = self.app.cfg.get("seasonal_override", "")
        i = self.seasonal_combo.findData(ov)
        if i >= 0:
            self.seasonal_combo.setCurrentIndex(i)
        self.seasonal_combo.currentIndexChanged.connect(
            lambda i: self.app.set_cfg("seasonal_override",
                                       self.seasonal_combo.itemData(i) or "")
        )
        form.addRow("Seasonal override", self.seasonal_combo)

        # Breathing / eye tracking / reactions
        self.breath_cb = QCheckBox("Idle breathing animation")
        self.breath_cb.setChecked(bool(self.app.cfg.get("breathing", True)))
        self.breath_cb.toggled.connect(
            lambda v: self.app.set_cfg("breathing", bool(v))
        )
        form.addRow("", self.breath_cb)

        self.eye_cb = QCheckBox("Eyes follow the cursor")
        self.eye_cb.setChecked(bool(self.app.cfg.get("eye_tracking", True)))
        self.eye_cb.toggled.connect(
            lambda v: self.app.set_cfg("eye_tracking", bool(v))
        )
        form.addRow("", self.eye_cb)

        self.react_cb = QCheckBox("Reaction bubbles (! / ?)")
        self.react_cb.setChecked(bool(self.app.cfg.get("reactions", True)))
        self.react_cb.toggled.connect(
            lambda v: self.app.set_cfg("reactions", bool(v))
        )
        form.addRow("", self.react_cb)

        return w

    def _tab_behaviour(self) -> QWidget:
        w = QWidget()
        box = QVBoxLayout(w)
        box.setContentsMargins(12, 16, 12, 12)
        box.setSpacing(4)

        self._add_check(box, "Follow my cursor", "follow")
        self._add_check(box, "Sit on the active window", "perch")
        self._add_check(box, "Comment on what I'm doing", "comments")
        self._add_check(box, "Beeps and boops (sound)", "sounds")
        self._add_check(box, "Battery & CPU awareness (needs psutil)", "sys_aware")

        box.addSpacing(12)
        info = QLabel(
            "Tip: right-click Orbi for quick actions; left-click to boop; "
            "double-click for rubber-duck debugging. Drag him anywhere."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: rgba(180,185,200,200); font-size: 11px;")
        box.addWidget(info)

        box.addStretch(1)
        return w

    def _tab_reminders(self) -> QWidget:
        w = QWidget()
        box = QVBoxLayout(w)
        box.setContentsMargins(12, 16, 12, 12)
        box.setSpacing(4)

        self._add_check(box, "Drink water (every 60 min)", "remind_water")
        self._add_check(box, "Stretch (every 45 min)", "remind_stretch")
        self._add_check(box, "Rest your eyes (every 20 min)", "remind_eyes")
        self._add_check(box, "Stand up (every 90 min)", "remind_stand")
        self._add_check(box, "Posture check (every 30 min)", "remind_posture")

        box.addSpacing(12)
        dnd_group = QGroupBox("Do not disturb")
        dnd = QHBoxLayout(dnd_group)
        dnd.addWidget(QLabel("Silence Orbi for"))
        self.dnd_spin = QSpinBox()
        self.dnd_spin.setRange(5, 480)
        self.dnd_spin.setValue(30)
        self.dnd_spin.setSuffix(" min")
        dnd.addWidget(self.dnd_spin)
        dnd_btn = QPushButton("Silence now")
        dnd_btn.clicked.connect(lambda: self.app.set_dnd(self.dnd_spin.value()))
        dnd.addWidget(dnd_btn)
        dnd.addStretch(1)
        box.addWidget(dnd_group)

        box.addStretch(1)
        return w

    def _tab_focus(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 16, 12, 12)

        self.work_spin = QSpinBox()
        self.work_spin.setRange(5, 180)
        self.work_spin.setValue(int(self.app.cfg.get("pomo_work_min", 25)))
        self.work_spin.setSuffix(" min")
        self.work_spin.valueChanged.connect(
            lambda v: self.app.set_cfg("pomo_work_min", int(v))
        )
        form.addRow("Focus block", self.work_spin)

        self.break_spin = QSpinBox()
        self.break_spin.setRange(1, 60)
        self.break_spin.setValue(int(self.app.cfg.get("pomo_break_min", 5)))
        self.break_spin.setSuffix(" min")
        self.break_spin.valueChanged.connect(
            lambda v: self.app.set_cfg("pomo_break_min", int(v))
        )
        form.addRow("Short break", self.break_spin)

        self.long_spin = QSpinBox()
        self.long_spin.setRange(5, 120)
        self.long_spin.setValue(int(self.app.cfg.get("pomo_long_break_min", 15)))
        self.long_spin.setSuffix(" min")
        self.long_spin.valueChanged.connect(
            lambda v: self.app.set_cfg("pomo_long_break_min", int(v))
        )
        form.addRow("Long break", self.long_spin)

        self.sess_spin = QSpinBox()
        self.sess_spin.setRange(1, 12)
        self.sess_spin.setValue(int(self.app.cfg.get("pomo_sessions_before_long", 4)))
        self.sess_spin.valueChanged.connect(
            lambda v: self.app.set_cfg("pomo_sessions_before_long", int(v))
        )
        form.addRow("Sessions before long break", self.sess_spin)

        self.stats_cb = QCheckBox("Track usage statistics")
        self.stats_cb.setChecked(bool(self.app.cfg.get("stats_enabled", True)))
        self.stats_cb.toggled.connect(
            lambda v: self.app.set_cfg("stats_enabled", bool(v))
        )
        form.addRow("", self.stats_cb)

        return w

    def _tab_git(self) -> QWidget:
        w = QWidget()
        box = QVBoxLayout(w)
        box.setContentsMargins(12, 16, 12, 12)

        self._add_check(box, "Watch repositories for commits", "git_watch")
        self._add_check(box, "Nudge me about uncommitted changes", "git_dirty_nudge")

        box.addSpacing(8)
        self.repo_list = QListWidget()
        self.repo_list.setMinimumHeight(160)
        box.addWidget(self.repo_list)
        self._refresh_repos()

        row = QHBoxLayout()
        add_btn = QPushButton("Add repository...")
        add_btn.clicked.connect(self._add_repo)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_repo)
        clear_btn = QPushButton("Clear all")
        clear_btn.clicked.connect(self._clear_repos)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addWidget(clear_btn)
        row.addStretch(1)
        box.addLayout(row)

        box.addStretch(1)
        return w

    def _tab_system(self) -> QWidget:
        w = QWidget()
        box = QVBoxLayout(w)
        box.setContentsMargins(12, 16, 12, 12)
        box.setSpacing(6)

        if IS_WIN:
            self.autostart_cb = QCheckBox("Start with Windows (recommended)")
            self.autostart_cb.setChecked(autostart_enabled())
            self.autostart_cb.toggled.connect(
                lambda v: set_autostart(bool(v))
            )
            box.addWidget(self.autostart_cb)

            hk_group = QGroupBox("Global hotkey")
            hk_box = QHBoxLayout(hk_group)
            self.hotkey_cb = QCheckBox("Enable")
            self.hotkey_cb.setChecked(bool(self.app.cfg.get("hotkey_enabled", False)))
            self.hotkey_cb.toggled.connect(self._toggle_hotkey)
            hk_box.addWidget(self.hotkey_cb)
            self.hotkey_edit = QLineEdit(self.app.cfg.get("hotkey", "Ctrl+Alt+O"))
            self.hotkey_edit.setMaxLength(32)
            self.hotkey_edit.setFixedWidth(160)
            self.hotkey_edit.editingFinished.connect(self._apply_hotkey)
            hk_box.addWidget(self.hotkey_edit)
            hk_box.addWidget(QLabel("(format: Ctrl+Alt+O)"))
            hk_box.addStretch(1)
            box.addWidget(hk_group)
        else:
            box.addWidget(QLabel("Start with system and global hotkey are Windows-only."))

        plugins_group = QGroupBox("Plugins")
        plug_box = QVBoxLayout(plugins_group)
        self.plugins_cb = QCheckBox("Enable plugins from the Orbi config folder")
        self.plugins_cb.setChecked(bool(self.app.cfg.get("plugins_enabled", False)))
        self.plugins_cb.toggled.connect(self._toggle_plugins)
        plug_box.addWidget(self.plugins_cb)
        self.plugins_label = QLabel("")
        self.plugins_label.setWordWrap(True)
        self.plugins_label.setStyleSheet("color: rgba(180,185,200,200); font-size: 11px;")
        plug_box.addWidget(self.plugins_label)
        open_btn = QPushButton("Open plugins folder")
        open_btn.clicked.connect(self._open_plugins_folder)
        plug_box.addWidget(open_btn)
        self._refresh_plugin_label()
        box.addWidget(plugins_group)

        box.addSpacing(8)
        util_row = QHBoxLayout()
        dance_btn = QPushButton("Dance!")
        dance_btn.clicked.connect(self.app.dance)
        util_row.addWidget(dance_btn)
        sleep_btn = QPushButton("Sleep / Wake")
        sleep_btn.clicked.connect(self.app.toggle_sleep)
        util_row.addWidget(sleep_btn)
        cfg_btn = QPushButton("Open config folder")
        cfg_btn.clicked.connect(self.app.open_config_folder)
        util_row.addWidget(cfg_btn)
        util_row.addStretch(1)
        box.addLayout(util_row)

        box.addSpacing(8)
        status_row = QHBoxLayout()
        stats_btn = QPushButton("View stats")
        stats_btn.clicked.connect(self.app.open_stats)
        status_row.addWidget(stats_btn)
        ach_btn = QPushButton("Achievements")
        ach_btn.clicked.connect(self.app.open_achievements)
        status_row.addWidget(ach_btn)
        status_row.addStretch(1)
        box.addLayout(status_row)

        box.addStretch(1)
        return w

    def _tab_about(self) -> QWidget:
        w = QWidget()
        box = QVBoxLayout(w)
        box.setContentsMargins(12, 16, 12, 12)
        box.setSpacing(8)

        title = QLabel("<h2 style='margin:0;'>Orbi %s</h2>" % APP_VERSION)
        box.addWidget(title)
        sub = QLabel(
            "A little round desktop companion. Frameless, always-on-top, "
            "and drawn entirely in code."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: rgba(180,185,200,220);")
        box.addWidget(sub)

        box.addSpacing(8)
        stats = self.app.achievements
        info = QLabel(
            "Achievements: %d / %d<br>"
            "Day streak: %d<br>"
            "Session uptime: %s"
            % (
                stats.unlocked_count(),
                stats.total(),
                self.app.stats.day_streak() if self.app.stats else 0,
                _format_duration(self.app.stats.session_seconds() if self.app.stats else 0),
            )
        )
        info.setWordWrap(True)
        box.addWidget(info)

        box.addSpacing(12)
        btns = QHBoxLayout()
        ach_btn = QPushButton("View achievements")
        ach_btn.clicked.connect(self.app.open_achievements)
        stats_btn = QPushButton("Detailed stats")
        stats_btn.clicked.connect(self.app.open_stats)
        btns.addWidget(ach_btn)
        btns.addWidget(stats_btn)
        btns.addStretch(1)
        box.addLayout(btns)

        box.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _add_check(self, layout: QVBoxLayout, text: str, key: str) -> None:
        cb = QCheckBox(text)
        cb.setChecked(bool(self.app.cfg.get(key)))
        cb.toggled.connect(lambda v, k=key: self.app.set_cfg(k, bool(v)))
        layout.addWidget(cb)

    def _refresh_repos(self) -> None:
        self.repo_list.clear()
        for r in self.app.cfg.get("repos") or []:
            self.repo_list.addItem(r)

    def _add_repo(self) -> None:
        d = QFileDialog.getExistingDirectory(
            None, "Pick a git repository for Orbi to watch"
        )
        if not d:
            return
        repos = self.app.cfg.setdefault("repos", [])
        if d not in repos:
            repos.append(d)
            save_config(self.app.cfg)
            self._refresh_repos()
            self.app.notify("Watching %s for commits." % os.path.basename(d))

    def _remove_repo(self) -> None:
        item = self.repo_list.currentItem()
        if not item:
            return
        repo = item.text()
        repos = self.app.cfg.get("repos") or []
        if repo in repos:
            repos.remove(repo)
            save_config(self.app.cfg)
            self.app.git_last.pop(repo, None)
            self.app._dirty_since.pop(repo, None)
            self._refresh_repos()

    def _clear_repos(self) -> None:
        self.app.cfg["repos"] = []
        self.app.git_last.clear()
        self.app._dirty_since.clear()
        save_config(self.app.cfg)
        self._refresh_repos()

    def _toggle_hotkey(self, on: bool) -> None:
        self.app.cfg["hotkey_enabled"] = bool(on)
        save_config(self.app.cfg)
        if on:
            ok = self.app.register_hotkey(self.hotkey_edit.text())
            if not ok:
                QMessageBox.warning(
                    self, "Hotkey",
                    "Could not register %s. Try a different combination."
                    % self.hotkey_edit.text(),
                )
                self.hotkey_cb.setChecked(False)
        else:
            self.app.unregister_hotkey()

    def _apply_hotkey(self) -> None:
        seq = self.hotkey_edit.text().strip()
        if not seq:
            return
        self.app.cfg["hotkey"] = seq
        save_config(self.app.cfg)
        if self.app.cfg.get("hotkey_enabled"):
            self.app.register_hotkey(seq)

    def _toggle_plugins(self, on: bool) -> None:
        self.app.cfg["plugins_enabled"] = bool(on)
        save_config(self.app.cfg)
        self.app.enable_plugins(bool(on))
        self._refresh_plugin_label()

    def _refresh_plugin_label(self) -> None:
        if not getattr(self.app, "plugins", None):
            self.plugins_label.setText(
                "Plugins are disabled. Drop .py files into the folder to add capabilities."
            )
            return
        n = len(self.app.plugins.plugins)
        errs = self.app.plugins.errors()
        text = "%d plugin%s loaded." % (n, "" if n == 1 else "s")
        if errs:
            text += "  Errors: " + "; ".join(errs[:3])
        self.plugins_label.setText(text)

    def _open_plugins_folder(self) -> None:
        try:
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(PLUGINS_DIR))
        except Exception:
            logging.exception("open plugins folder failed")

    def closeEvent(self, e) -> None:  # noqa: N802
        self.app.settings = None
        e.accept()


# ============================================================================
# DUCK DIALOG
# ============================================================================

class DuckDialog(QDialog):
    """Rubber-duck debugging chat. Not a chatbot — a Socratic mirror."""

    def __init__(self, on_send: Callable[[str], None],
                 on_close: Callable[[], None]):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.CustomizeWindowHint,
        )
        self.setWindowTitle("Rubber duck debugging with Orbi")
        self.setMinimumSize(480, 260)
        self.setStyleSheet(dialog_qss())

        self.on_send = on_send
        self.on_close = on_close
        self._closed = False
        self.lines: List[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        header = QLabel(
            "Explain the problem out loud (well, in text). I'll ask the "
            "questions you already know the answers to. Press Enter to "
            "send, Esc when you're done."
        )
        header.setWordWrap(True)
        header.setStyleSheet("color: rgba(180,185,200,220); font-size: 11px;")
        lay.addWidget(header)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMinimumHeight(140)
        lay.addWidget(self.transcript, 1)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("What's going wrong?")
        self.edit.returnPressed.connect(self._send)
        lay.addWidget(self.edit)

        row = QHBoxLayout()
        row.addStretch(1)
        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self.close)
        row.addWidget(done_btn)
        lay.addLayout(row)

        self._append_system(
            "Orbi: Hi! Tell me what you're trying to do, and what's happening instead."
        )

    def _append_system(self, line: str) -> None:
        self.lines.append(line)
        self.transcript.setPlainText("\n\n".join(self.lines))
        # scroll to bottom
        sb = self.transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _send(self) -> None:
        text = self.edit.text().strip()
        if not text:
            return
        self.edit.clear()
        self._append_system("You: " + text)
        try:
            self.on_send(text)
        except Exception:
            logging.exception("duck on_send failed")

    def show_reply(self, text: str) -> None:
        self._append_system("Orbi: " + text)

    def reject(self) -> None:
        self.close()

    def closeEvent(self, e) -> None:  # noqa: N802
        if not self._closed:
            self._closed = True
            try:
                self.on_close()
            except Exception:
                logging.exception("duck on_close failed")
        e.accept()


# ============================================================================
# ACHIEVEMENTS DIALOG
# ============================================================================

class AchievementsDialog(QDialog):
    def __init__(self, achievements: Achievements):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setWindowTitle("Orbi Achievements")
        self.setMinimumSize(520, 520)
        self.setStyleSheet(dialog_qss())
        self.achievements = achievements

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        header = QLabel(
            "<b>%d / %d unlocked</b>"
            % (achievements.unlocked_count(), achievements.total())
        )
        lay.addWidget(header)

        pbar = QProgressBar()
        pbar.setRange(0, achievements.total())
        pbar.setValue(achievements.unlocked_count())
        lay.addWidget(pbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QVBoxLayout(inner)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(8)

        for a in ACHIEVEMENTS:
            grid.addWidget(self._make_row(a))

        grid.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        reset = QPushButton("Reset progress")
        reset.clicked.connect(self._reset)
        bottom.addWidget(reset)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

    def _make_row(self, a: Achievement) -> QWidget:
        unlocked = self.achievements.is_unlocked(a.key)
        count = self.achievements.counter(a.counter)
        progress = min(1.0, count / max(1, a.threshold))

        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        row.setStyleSheet(
            "QFrame { background: rgba(255,255,255,%d); border-radius: 6px; "
            "border: 1px solid rgba(255,255,255,20); }"
            % (26 if unlocked else 10)
        )
        v = QVBoxLayout(row)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(2)

        title = QLabel(
            ("\u2713  " if unlocked else "\u25CB  ") + "<b>%s</b>" % a.title
        )
        title.setStyleSheet(
            "color: %s;" % ("rgb(130, 255, 175)" if unlocked
                            else "rgb(230, 233, 242)")
        )
        v.addWidget(title)

        desc = QLabel(a.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: rgba(180,185,200,220); font-size: 11px;")
        v.addWidget(desc)

        if not unlocked:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(progress * 100))
            bar.setFixedHeight(6)
            v.addWidget(bar)
            counter = QLabel("%d / %d" % (count, a.threshold))
            counter.setStyleSheet("color: rgba(180,185,200,180); font-size: 10px;")
            v.addWidget(counter)

        return row

    def _reset(self) -> None:
        if QMessageBox.question(
            self, "Reset achievements",
            "Reset all achievement progress? This cannot be undone."
        ) == QMessageBox.StandardButton.Yes:
            self.achievements.reset()
            self.accept()


# ============================================================================
# STATS DIALOG
# ============================================================================

class StatsDialog(QDialog):
    def __init__(self, stats: Stats, achievements: Achievements):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setWindowTitle("Orbi Stats")
        self.setMinimumSize(480, 460)
        self.setStyleSheet(dialog_qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        title = QLabel("<h2 style='margin:0;'>Session &amp; History</h2>")
        lay.addWidget(title)

        info = QLabel(
            "<table cellpadding='6'>"
            "<tr><td><b>Uptime this session</b></td><td>%s</td></tr>"
            "<tr><td><b>Day streak</b></td><td>%d</td></tr>"
            "<tr><td><b>Pomodoros today</b></td><td>%d</td></tr>"
            "<tr><td><b>Pomodoros all-time</b></td><td>%d</td></tr>"
            "<tr><td><b>Achievements unlocked</b></td><td>%d / %d</td></tr>"
            "</table>"
            % (
                _format_duration(stats.session_seconds()),
                stats.day_streak(),
                int(stats.data.get("pomodoros_today", 0)),
                int(stats.data.get("pomodoros_total", 0)),
                achievements.unlocked_count(),
                achievements.total(),
            )
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        top_lbl = QLabel("<b>Top applications</b>")
        lay.addWidget(top_lbl)
        top_list = QListWidget()
        for exe, secs in stats.top_apps(8):
            top_list.addItem("%s   \u2014   %s" % (exe, _format_duration(int(secs))))
        lay.addWidget(top_list, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        open_cfg = QPushButton("Open config folder")
        open_cfg.clicked.connect(
            lambda: os.startfile(str(CONFIG_DIR))
        )
        bottom.addWidget(open_cfg)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)


# ============================================================================
# FORMAT HELPERS
# ============================================================================

def _format_duration(seconds: int) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return "%dh %dm %ds" % (h, m, s)
    if m > 0:
        return "%dm %ds" % (m, s)
    return "%ds" % s


# ============================================================================
# ORBI APP CONTROLLER
# ============================================================================


class OrbiApp:
    """The controller. Owns the QApplication, the Sim, the window, the tray,
    the QTimers, the CLI server, the settings dialog, and everything else.
    """

    def __init__(self, app: QApplication):
        self.app = app
        self.cfg = load_config()

        # --- theme & body visuals ---------------------------------------
        rebuild_body_visuals(self.cfg.get("theme", "classic"))

        # --- sim ---------------------------------------------------------
        self.sim = Sim(self.cfg.get("scale", 1.0))
        self.sim.follow = bool(self.cfg.get("follow"))
        self.sim.perch_enabled = bool(self.cfg.get("perch"))
        self.sim.breathing = bool(self.cfg.get("breathing", True))
        self.sim.eye_tracking = bool(self.cfg.get("eye_tracking", True))
        self.sim.reactions = bool(self.cfg.get("reactions", True))

        # --- sounds ------------------------------------------------------
        self.sounds = Sounds(bool(self.cfg.get("sounds")))

        # --- stats & achievements ----------------------------------------
        self.stats: Optional[Stats] = (
            Stats(bool(self.cfg.get("stats_enabled", True)))
            if self.cfg.get("stats_enabled", True) else None
        )
        self.achievements = Achievements(on_unlock=self._on_achievement)

        # --- window ------------------------------------------------------
        self.win = OrbiWindow(self.sim)
        self.win.theme_name = self.cfg.get("theme", "classic")
        self.win.seasonal_enabled = bool(self.cfg.get("seasonal", True))
        self.win.seasonal_override = self.cfg.get("seasonal_override", "") or ""
        self.win.settings_cb = self.open_settings
        self.win.double_cb = self.open_duck

        # --- dialogs (lazily created) ------------------------------------
        self.settings: Optional[SettingsDialog] = None
        self.duck: Optional[DuckDialog] = None
        self.duck_brain: Optional[DuckBrain] = None
        self.achievements_dialog: Optional[AchievementsDialog] = None
        self.stats_dialog: Optional[StatsDialog] = None

        # --- state -------------------------------------------------------
        self.asleep = False
        self.fg: Optional[Dict[str, Any]] = None
        self.fg_key: Optional[Tuple[str, str]] = None
        self.last_comment = 0.0
        self.pomo: Optional[Dict[str, Any]] = None
        self.build_t0: Optional[float] = None
        self.build_last_line = 0.0
        self.git_last: Dict[str, str] = {}
        self._dirty_since: Dict[str, float] = {}
        self._last_dirty_warn = 0.0
        self._last_dirty_check = 0.0
        self.dnd_until = 0.0
        self._last_batt_warn = 0.0
        self._last_cpu_warn = 0.0
        self._cpu_hi = 0
        self._last_sys = 0.0
        self.last = time.perf_counter()
        self._n = 0
        self._launch_time = time.time()

        # --- plugins -----------------------------------------------------
        self.plugins: Optional[PluginLoader] = None
        if self.cfg.get("plugins_enabled"):
            self.plugins = PluginLoader(self, enabled=True)

        # --- hotkey ------------------------------------------------------
        self.hotkey: Optional[GlobalHotkey] = None
        if IS_WIN and self.cfg.get("hotkey_enabled"):
            self._setup_hotkey()

        # --- tray --------------------------------------------------------
        self.tray = QSystemTrayIcon(
            make_tray_icon(self.cfg.get("theme", "classic")), app
        )
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.build_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.setToolTip("Orbi")
        self.tray.show()

        # --- initial placement ------------------------------------------
        work = self.work_rect()
        self.sim.work = work
        self.sim.x = work[0] + (work[2] - work[0]) * 0.7
        self.sim.y = self.sim.ground = work[3]
        self.win.sync(work)
        self.win.show()
        self.win.sync(work)

        # --- timers ------------------------------------------------------
        self.timer = QTimer()
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        self._slow("fg", 400, self.fg_poll)
        self._slow("idle", 5000, self.idle_poll)
        self._slow("git", 5000, self.git_poll)
        self._slow("pomo", 1000, self.pomo_tick)
        self._slow("build", 1000, self.build_tick)
        self._slow("sys", 15000, self.sys_poll)
        self._slow("stats", 30000, self._stats_flush)
        self._slow("plugins", 60000, self._plugin_poll)
        self._slow("seasonal", 60000, self._seasonal_tick)

        # reminders
        self._reminder_timers: List[QTimer] = []
        for key, minutes, lines in REMINDERS:
            t = QTimer()
            t.setInterval(minutes * 60 * 1000)
            t.timeout.connect(
                lambda k=key, ls=lines: self.fire_reminder(k, ls)
            )
            t.start()
            self._reminder_timers.append(t)

        # --- local command server ---------------------------------------
        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER_NAME)
        self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._on_conn)

        # --- first-run banner + greeting --------------------------------
        if not self.cfg.get("first_run_done"):
            self.tray.showMessage(
                "Orbi is running",
                "Find me under the ^ arrow. Right-click ME (the little "
                "robot) for settings. Tray icon click wakes me up.",
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )
            self.cfg["first_run_done"] = True
            save_config(self.cfg)

        # achievement: first_steps
        self.achievements.bump("launches")
        self._mark_time_of_day()

        QTimer.singleShot(
            700,
            lambda: self.say(
                "Beep boop! Orbi online. Right-click me for settings, tray icon to sleep.",
                5.5, urgent=True, sound="wake",
            ),
        )

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------
    def _slow(self, name: str, ms: int, fn: Callable[[], None]) -> None:
        t = QTimer()
        t.setInterval(ms)
        t.timeout.connect(fn)
        t.start()
        setattr(self, "_t_" + name, t)

    def screen(self):
        return QGuiApplication.primaryScreen()

    def work_rect(self) -> Tuple[int, int, int, int]:
        g = self.screen().availableGeometry()
        return (g.left(), g.top(), g.right() + 1, g.bottom() + 1)

    def say(self, text: str, dur: Optional[float] = None,
            urgent: bool = False, sound: Optional[str] = None) -> None:
        if self.asleep:
            return
        self.sim.say(text, dur, urgent)
        if sound:
            self.sounds.play(sound)

    def notify(self, text: str) -> None:
        if self.dnd_active() and not self.asleep:
            return
        if self.asleep:
            self.tray.showMessage(
                "Orbi", text, QSystemTrayIcon.MessageIcon.Information, 4000
            )
        else:
            self.say(text, urgent=True)

    def _on_achievement(self, a: Achievement) -> None:
        self.sounds.play("achievement")
        if not self.asleep:
            self.sim.celebrate()
            self.sim.stars(8)
            self.say("Achievement unlocked: %s!" % a.title, 5.0, urgent=True)
        else:
            self.tray.showMessage(
                "Achievement unlocked!",
                a.title,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _mark_time_of_day(self) -> None:
        h = datetime.now().hour
        if h >= 0 and h < 6:
            self.achievements.bump("night_uses")
        elif h >= 5 and h < 8:
            self.achievements.bump("early_uses")
        if self.stats and self.stats.session_seconds() >= 86400:
            self.achievements.bump("long_sessions")

    # ------------------------------------------------------------------
    # DND
    # ------------------------------------------------------------------
    def dnd_active(self) -> bool:
        return time.time() < self.dnd_until

    def set_dnd(self, minutes: int) -> None:
        self.dnd_until = time.time() + max(1, minutes) * 60
        self.notify("Do-not-disturb for %d minutes." % minutes)

    # ------------------------------------------------------------------
    # main tick
    # ------------------------------------------------------------------
    def tick(self) -> None:
        now = time.perf_counter()
        dt = now - self.last
        self.last = now

        work = self.work_rect()
        cur = QCursor.pos()
        dpr = self.screen().devicePixelRatio() or 1.0

        perch = None
        fg = self.fg
        if fg and not fg["zoomed"]:
            l, t, r, _ = fg["rect"]
            l, t, r = l / dpr, t / dpr, r / dpr
            if r - l >= 300 and t >= work[1]:
                perch = (l, t, r)

        typing = idle_seconds() < 1.5
        env = Env(work, (cur.x(), cur.y()), perch, typing=typing)
        self.sim.update(dt, env)
        self.win.sync(work)

        # periodic click-through toggle
        self._n += 1
        if self._n % 3 == 0:
            try:
                inside = self.win.hit_rect().contains(
                    QPointF(self.win.mapFromGlobal(cur))
                )
                holding = self.win._press is not None
                set_click_through(int(self.win.winId()), not (inside or holding))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # polling: foreground window
    # ------------------------------------------------------------------
    def fg_poll(self) -> None:
        if self.asleep:
            return
        self.fg = foreground_info()
        if not self.fg:
            return
        key = (self.fg["exe"], self.fg["title"])
        if key != self.fg_key:
            self.fg_key = key
            self.maybe_comment(self.fg)
        if self.stats:
            self.stats.note_foreground(self.fg["exe"], 0.4)

    def maybe_comment(self, info: Dict[str, Any]) -> None:
        if (
            not self.cfg.get("comments")
            or self.sim.drowsy
            or self.sim.focus
            or self.dnd_active()
        ):
            return
        if time.time() - self.last_comment < 180:
            return
        if random.random() > 0.45:
            return
        text = quip_for(info.get("exe"), info.get("title"))
        if text:
            self.last_comment = time.time()
            self.say(text, 4.5)

    # ------------------------------------------------------------------
    # polling: idle (sleepy)
    # ------------------------------------------------------------------
    def idle_poll(self) -> None:
        if self.asleep:
            return
        idle = idle_seconds()
        if idle >= 600 and not self.sim.drowsy:
            self.sim.drowsy = True
            self.say("*yaaawn* ...zzz", 3)
        elif idle < 5 and self.sim.drowsy:
            self.sim.drowsy = False
            self.sim.set_mood("alert", 1.5)
            self.say(
                "Oh! You're back. I wasn't sleeping. (I was sleeping.)",
                4, urgent=True, sound="boop",
            )

    # ------------------------------------------------------------------
    # polling: git
    # ------------------------------------------------------------------
    def git_poll(self) -> None:
        if not self.cfg.get("git_watch"):
            return
        repos = self.cfg.get("repos") or []
        if not repos:
            return
        creationflags = 0x08000000 if IS_WIN else 0
        for repo in list(repos):
            try:
                out = subprocess.run(
                    ["git", "-C", repo, "reflog", "-1", "--format=%H|%gs"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=creationflags,
                )
                if out.returncode != 0:
                    continue
                h, _, subj = out.stdout.strip().partition("|")
                prev = self.git_last.get(repo)
                self.git_last[repo] = h
                if not prev or prev == h or not subj.startswith("commit"):
                    continue
                msg = subprocess.run(
                    ["git", "-C", repo, "log", "-1", "--format=%s"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=creationflags,
                ).stdout.strip()
                self.achievements.bump("commits")
                if not self.asleep:
                    self.sim.celebrate('Commit! "%s"' % (msg[:60] or "..."))
                    self.sounds.play("commit")
            except Exception:
                continue

        # dirty-tree nudge (rate limited)
        if not self.cfg.get("git_dirty_nudge"):
            return
        now = time.time()
        if now - self._last_dirty_check < 300:
            return
        self._last_dirty_check = now
        for repo in repos:
            try:
                r = subprocess.run(
                    ["git", "-C", repo, "status", "--porcelain"],
                    capture_output=True, text=True, timeout=4,
                    creationflags=creationflags,
                )
                if r.returncode != 0:
                    continue
                if r.stdout.strip():
                    first = self._dirty_since.setdefault(repo, now)
                    if now - first > 5400 and now - self._last_dirty_warn > 3600:
                        self._last_dirty_warn = now
                        self.notify(
                            "%s has uncommitted changes. Commit or stash?"
                            % os.path.basename(repo)
                        )
                else:
                    self._dirty_since.pop(repo, None)
            except Exception:
                continue

    # ------------------------------------------------------------------
    # polling: system (battery/CPU)
    # ------------------------------------------------------------------
    def sys_poll(self) -> None:
        if not HAS_PSUTIL or not self.cfg.get("sys_aware") or self.asleep:
            return
        try:
            now = time.time()
            batt = psutil.sensors_battery()
            if batt is not None:
                if (
                    batt.percent <= 20
                    and not batt.power_plugged
                    and now - self._last_batt_warn > 1800
                ):
                    self._last_batt_warn = now
                    self.notify("Battery at %d%%. Plug in soon." % int(batt.percent))
                    self.sounds.play("alert")
            cpu = psutil.cpu_percent(interval=None)
            if cpu > 92:
                self._cpu_hi += 1
            else:
                self._cpu_hi = 0
            if self._cpu_hi >= 3 and now - self._last_cpu_warn > 1800:
                self._last_cpu_warn = now
                self._cpu_hi = 0
                self.notify("CPU is pinned. Something is chewing cycles.")
        except Exception:
            logging.exception("sys_poll failed")

    # ------------------------------------------------------------------
    # reminders
    # ------------------------------------------------------------------
    def fire_reminder(self, key: str, lines: List[str]) -> None:
        if (
            self.asleep
            or not self.cfg.get(key)
            or self.sim.drowsy
            or self.dnd_active()
            or idle_seconds() > 180
        ):
            return
        self.say(random.choice(lines), 6.5, urgent=True, sound="boop")
        self.sim.set_mood("happy", 2.0)
        self.sim.bounce = 1.0
        self.sim.squash_v -= 3.0
        if key == "remind_water":
            self.achievements.bump("waters_drunk")
        elif key == "remind_stretch":
            self.achievements.bump("stretches")
        elif key == "remind_eyes":
            self.achievements.bump("eye_rests")

    # ------------------------------------------------------------------
    # pomodoro
    # ------------------------------------------------------------------
    def pomo_toggle(self) -> None:
        if self.pomo:
            self.pomo = None
            self.sim.focus = False
            self.tray.setToolTip("Orbi")
            self.notify("Focus timer stopped.")
        else:
            minutes = int(self.cfg.get("pomo_work_min", 25))
            self.pomo = {"phase": "work", "end": time.time() + minutes * 60, "n": 0}
            self.sim.focus = True
            self.notify("Focus time! %d minutes. I'll stay quiet." % minutes)

    def pomo_tick(self) -> None:
        if not self.pomo:
            return
        rem = int(self.pomo["end"] - time.time())
        label = "Focus" if self.pomo["phase"] == "work" else "Break"
        self.tray.setToolTip(
            "Orbi - %s %d:%02d" % (label, max(0, rem) // 60, max(0, rem) % 60)
        )
        if rem > 0:
            return

        if self.pomo["phase"] == "work":
            self.pomo["n"] += 1
            self._bump_pomo_count()
            self.achievements.bump("pomodoros")
            if self.stats:
                self.stats.bump_pomodoro()

            long_every = int(self.cfg.get("pomo_sessions_before_long", 4))
            long_break = (self.pomo["n"] % long_every == 0)
            minutes = int(self.cfg.get("pomo_long_break_min", 15)) if long_break \
                else int(self.cfg.get("pomo_break_min", 5))
            self.pomo.update(phase="break", end=time.time() + minutes * 60)
            self.sim.focus = False
            text = "Break time! Stand up, stretch, hydrate. (%d min)" % minutes
            if self.asleep:
                self.tray.showMessage("Orbi", text)
            else:
                self.sim.celebrate(text)
                self.sounds.play("happy")
        else:
            minutes = int(self.cfg.get("pomo_work_min", 25))
            self.pomo.update(phase="work", end=time.time() + minutes * 60)
            self.sim.focus = True
            self.notify("Break's over. Next focus block: %d minutes." % minutes)
            self.sounds.play("alert")

    def _bump_pomo_count(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.cfg.get("pomo_date") != today:
            self.cfg["pomo_date"] = today
            self.cfg["pomo_count"] = 0
        self.cfg["pomo_count"] = int(self.cfg.get("pomo_count", 0)) + 1
        self.cfg["pomo_total"] = int(self.cfg.get("pomo_total", 0)) + 1
        save_config(self.cfg)

    # ------------------------------------------------------------------
    # build watcher
    # ------------------------------------------------------------------
    def build_tick(self) -> None:
        if self.build_t0 is None or self.asleep:
            return
        el = time.time() - self.build_t0
        if el > 300:
            self.achievements.bump("nervous_builds")
        if el > 8:
            self.sim.nervous = True
            if time.time() - self.build_last_line > 20:
                self.build_last_line = time.time()
                self.say(
                    random.choice(BUILD_NERVOUS), 4.5,
                    urgent=True, sound="alert",
                )

    # ------------------------------------------------------------------
    # periodic flush / polls
    # ------------------------------------------------------------------
    def _stats_flush(self) -> None:
        if self.stats:
            self.stats.save_periodic()
            self.achievements.set_counter("day_streak", self.stats.day_streak())
            self._mark_time_of_day()

    def _plugin_poll(self) -> None:
        if self.plugins:
            self.plugins.poll()

    def _seasonal_tick(self) -> None:
        season = current_season()
        if season != getattr(self, "_last_season", None):
            self._last_season = season

    # ------------------------------------------------------------------
    # CLI / tray command dispatch
    # ------------------------------------------------------------------
    def _on_conn(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock: QLocalSocket) -> None:
        data = bytes(sock.readAll()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.handle_command(line)

    def handle_command(self, line: str) -> None:
        cmd, _, arg = line.strip().partition(" ")
        cmd = cmd.lower()

        # plugins get first look
        if self.plugins and self.plugins.dispatch(cmd, arg):
            return

        if cmd == "wake":
            self.wake_up()
        elif cmd == "sleep":
            self.go_sleep()
        elif cmd == "quit":
            self.quit()
        elif cmd == "say":
            self.notify(arg or "...")
        elif cmd == "dnd":
            try:
                mins = int(arg) if arg.strip() else 30
            except ValueError:
                mins = 30
            self.set_dnd(mins)
        elif cmd == "status":
            self.notify(arg or "")
        elif cmd == "focus":
            if not self.pomo:
                self.pomo_toggle()
        elif cmd == "relax":
            if self.pomo:
                self.pomo_toggle()
        elif cmd == "pass":
            self.achievements.bump("pass_runs")
            if self.asleep:
                self.notify(random.choice(PASS_LINES))
            else:
                self.sim.celebrate(random.choice(PASS_LINES))
                self.sounds.play("happy")
        elif cmd == "fail":
            self.achievements.bump("fail_runs")
            if self.asleep:
                self.notify(random.choice(FAIL_LINES))
            else:
                self.sim.sad(random.choice(FAIL_LINES))
                self.sounds.play("sad")
        elif cmd == "celebrate":
            if not self.asleep:
                self.sim.celebrate(arg or "Woohoo!")
        elif cmd == "build-start":
            self.build_t0, self.build_last_line = time.time(), 0.0
            self.achievements.bump("builds")
            self.say("Build started. I'll keep an eye on it.", 3)
        elif cmd in ("build-done", "build-fail"):
            was_nervous = self.sim.nervous
            self.build_t0 = None
            self.sim.nervous = False
            if self.asleep:
                return
            if cmd == "build-fail":
                self.sim.sad("Build failed. I'm sure it's a typo.")
                self.sounds.play("sad")
            elif was_nervous:
                self.sim.celebrate("Phew! Build finished.")
                self.sounds.play("happy")
            else:
                self.say("Build done.", 2.5)
        elif cmd == "love":
            if not self.sleep:
                self.sim.love(arg or None, 3.0)
        elif cmd == "think":
            if not self.asleep:
                self.sim.begin_think(dur=5.0)
        elif cmd =="alert":
            if not self.asleep:
                self.sim.set_mood("alert", 2.5)
        elif cmd == 'bored':
            if not self.asleep:
                self.sim.set_mood("bored", 4.0)

    # ------------------------------------------------------------------
    # sleep / wake
    # ------------------------------------------------------------------
    def go_sleep(self) -> None:
        if self.asleep or self.sim.sleep_dir > 0:
            return
        self.sounds.play("sleep")
        self.sim.nap(self._on_asleep)

    def _on_asleep(self) -> None:
        self.asleep = True
        self.win.hide()
        self.timer.stop()
        self.tray.setToolTip("Orbi (sleeping) - click to wake")
        self.tray.setIcon(make_tray_icon(self.cfg.get("theme", "classic"),
                                         sleeping=True))
        self.achievements.bump("naps")

    def wake_up(self) -> None:
        if self.asleep:
            self.asleep = False
            work = self.work_rect()
            self.sim.mode = "ground"
            self.sim.hop = None
            self.sim.hop_h = 0.0
            self.sim.vy = 0.0
            self.sim.x = min(
                max(self.sim.x, work[0] + self.sim.R + 8),
                work[2] - self.sim.R - 8,
            )
            self.sim.y = self.sim.ground = work[3]
            self.last = time.perf_counter()
            self.win.sync(work)
            self.win.show()
            self.timer.start()
            self.tray.setIcon(make_tray_icon(self.cfg.get("theme", "classic")))
            self.tray.setToolTip("Orbi")
        elif self.sim.sleep_dir > 0:
            self.sim.on_asleep = None
        self.sim.wake()
        self.sounds.play("wake")
        self.say("Beep boop! I'm awake.", 2.5, urgent=True)

    def toggle_sleep(self) -> None:
        if self.asleep or self.sim.sleep_dir > 0:
            self.wake_up()
        else:
            self.go_sleep()

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_sleep()

    # ------------------------------------------------------------------
    # rubber duck
    # ------------------------------------------------------------------
    def open_duck(self) -> None:
        if self.asleep:
            self.wake_up()
        if self.duck is not None:
            self.duck.raise_()
            self.duck.activateWindow()
            return
        self.duck_brain = DuckBrain()
        self.duck = DuckDialog(self.duck_message, self.duck_closed)
        self._center_dialog_near_orbi(self.duck)

        self.duck.show()
        self.duck.raise_()
        self.duck.activateWindow()
        self.duck.edit.setFocus()
        self.sim.listening = True
        self.achievements.bump("duck_sessions")
        self.say(
            "I'm all ears (well, one lens). What are you trying to do?",
            5, urgent=True, sound="question",
        )

    def _center_dialog_near_orbi(self, dlg: QDialog) -> None:
        g = self.screen().availableGeometry()
        w = self.win
        x = int(min(max(g.left() + 10, w.ox + w.W / 2 - 440), g.right() - 460))
        y = int(max(g.top() + 10, w.oy + w.H - 320))
        dlg.move(x, y)

    def duck_message(self, text: str) -> None:
        if not self.duck_brain:
            return
        reply, eureka = self.duck_brain.reply(text)
        if self.duck:
            self.duck.show_reply(reply)
        self.sim.say(reply, max(4.0, 1.6 + 0.06 * len(reply)), urgent=True)
        if eureka:
            self.achievements.bump("eurekas")
            self.sim.celebrate()
            self.sounds.play("happy")
        # optional log
        if self.cfg.get("duck_log"):
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CHAT_LOG, "a", encoding="utf-8") as f:
                    f.write("[%s]\nYOU: %s\nORBI: %s\n\n"
                            % (datetime.now().isoformat(timespec="seconds"),
                               text, reply))
            except Exception:
                pass

    def duck_closed(self) -> None:
        self.duck = None
        self.sim.listening = False
        self.say("Anytime! Rubber duck rates: free.", 3)

    # ------------------------------------------------------------------
    # dialogs
    # ------------------------------------------------------------------
    def open_settings(self) -> None:
        if self.asleep:
            self.wake_up()
        if self.settings is not None:
            self.settings.raise_()
            self.settings.activateWindow()
            return
        self.settings = SettingsDialog(self)
        self._center_dialog_near_orbi(self.settings)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def open_achievements(self) -> None:
        if self.achievements_dialog is not None:
            self.achievements_dialog.raise_()
            self.achievements_dialog.activateWindow()
            return
        self.achievements_dialog = AchievementsDialog(self.achievements)
        self._center_dialog_near_orbi(self.achievements_dialog)
        self.achievements_dialog.show()
        self.achievements_dialog.raise_()
        self.achievements_dialog.activateWindow()
        self.achievements_dialog.finished.connect(
            lambda: setattr(self, "achievements_dialog", None)
        )

    def open_stats(self) -> None:
        if not self.stats:
            return
        if self.stats_dialog is not None:
            self.stats_dialog.raise_()
            self.stats_dialog.activateWindow()
            return
        self.stats_dialog = StatsDialog(self.stats, self.achievements)
        self._center_dialog_near_orbi(self.stats_dialog)
        self.stats_dialog.show()
        self.stats_dialog.raise_()
        self.stats_dialog.activateWindow()
        self.stats_dialog.finished.connect(
            lambda: setattr(self, "stats_dialog", None)
        )

    def open_config_folder(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(CONFIG_DIR))
        except Exception:
            logging.exception("open config folder failed")

    def dance(self) -> None:
        if not self.asleep:
            self.sim.celebrate("Beep boop beep!")

    # ------------------------------------------------------------------
    # hotkey
    # ------------------------------------------------------------------
    def _setup_hotkey(self) -> None:
        self.hotkey = GlobalHotkey()
        self.hotkey.activated.connect(self._hotkey_pressed)
        ok = self.register_hotkey(self.cfg.get("hotkey", "Ctrl+Alt+O"))
        if not ok:
            logging.warning("failed to register hotkey")

    def register_hotkey(self, seq: str) -> bool:
        if not IS_WIN:
            return False
        if self.hotkey is None:
            self.hotkey = GlobalHotkey()
            self.hotkey.activated.connect(self._hotkey_pressed)
        return self.hotkey.register(seq)

    def unregister_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.unregister()

    def _hotkey_pressed(self) -> None:
        self.toggle_sleep()

    # ------------------------------------------------------------------
    # plugins
    # ------------------------------------------------------------------
    def enable_plugins(self, on: bool) -> None:
        if on:
            if self.plugins is None:
                self.plugins = PluginLoader(self, enabled=True)
            else:
                self.plugins.enabled = True
                self.plugins.load()
        else:
            if self.plugins:
                self.plugins.enabled = False
                self.plugins.plugins = []

    # ------------------------------------------------------------------
    # settings callbacks
    # ------------------------------------------------------------------
    def set_cfg(self, key: str, value: Any) -> None:
        self.cfg[key] = value
        save_config(self.cfg)
        if key == "follow":
            self.sim.follow = bool(value)
        elif key == "perch":
            self.sim.perch_enabled = bool(value)
        elif key == "sounds":
            self.sounds.enabled = bool(value)
            if value:
                self.sounds.play("boop")
        elif key == "breathing":
            self.sim.breathing = bool(value)
        elif key == "eye_tracking":
            self.sim.eye_tracking = bool(value)
        elif key == "reactions":
            self.sim.reactions = bool(value)
        elif key == "seasonal":
            self.win.seasonal_enabled = bool(value)
        elif key == "seasonal_override":
            self.win.seasonal_override = str(value or "")
        elif key == "stats_enabled":
            if value and self.stats is None:
                self.stats = Stats(True)
            elif not value and self.stats is not None:
                self.stats.close()
                self.stats = None

    def set_theme(self, name: str) -> None:
        if name not in THEMES:
            return
        self.cfg["theme"] = name
        save_config(self.cfg)
        rebuild_body_visuals(name)
        self.win.theme_name = name
        if not self.asleep:
            self.tray.setIcon(make_tray_icon(name))
        else:
            self.tray.setIcon(make_tray_icon(name, sleeping=True))
        self.win.update()
        # achievement: themes_used
        used = self.achievements.data.setdefault("counters", {})
        # count distinct themes by keeping a set in the counters dict
        seen = set(self.cfg.get("_themes_seen") or [])
        seen.add(name)
        self.cfg["_themes_seen"] = sorted(seen)
        save_config(self.cfg)
        used["themes_used"] = len(seen)
        save_achievements(self.achievements.data)
        self.achievements._check_unlocks()

    def set_scale(self, v: float) -> None:
        self.cfg["scale"] = float(v)
        save_config(self.cfg)
        self.sim.set_scale(v)
        self.win.apply_scale()

    # ------------------------------------------------------------------
    # tray menu
    # ------------------------------------------------------------------
    def build_menu(self) -> None:
        m = self.menu
        m.clear()

        sleeping = self.asleep or self.sim.sleep_dir > 0
        m.addAction("Wake Orbi" if sleeping else "Sleep Orbi (hide)").triggered.connect(
            self.toggle_sleep
        )
        m.addSeparator()
        m.addAction("Settings...").triggered.connect(self.open_settings)
        m.addAction("Rubber duck mode: talk it out...").triggered.connect(self.open_duck)

        if self.pomo:
            rem = max(0, int(self.pomo["end"] - time.time()))
            today = self.cfg.get("pomo_count", 0)
            label = "Stop focus timer (%s %d:%02d left \u2022 %d today)" % (
                "focus" if self.pomo["phase"] == "work" else "break",
                rem // 60, rem % 60, today,
            )
        else:
            today = self.cfg.get("pomo_count", 0) \
                if self.cfg.get("pomo_date") == datetime.now().strftime("%Y-%m-%d") else 0
            label = "Start focus timer (25 / 5) \u2022 %d today" % today
        m.addAction(label).triggered.connect(self.pomo_toggle)

        if self.dnd_active():
            remain = int((self.dnd_until - time.time()) // 60)
            m.addAction("Do-not-disturb for %d more min" % remain).setEnabled(False)
        else:
            m.addAction("Do-not-disturb 30 min").triggered.connect(
                lambda: self.set_dnd(30)
            )

        m.addAction("Dance!").triggered.connect(self.dance)
        m.addAction("Achievements...").triggered.connect(self.open_achievements)
        if self.stats:
            m.addAction("Stats...").triggered.connect(self.open_stats)
        m.addSeparator()
        m.addAction("Open config folder").triggered.connect(self.open_config_folder)
        m.addSeparator()
        m.addAction("Quit Orbi").triggered.connect(self.quit)

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def quit(self) -> None:
        try:
            if self.stats:
                self.stats.close()
            if self.hotkey:
                self.hotkey.unregister()
            self.tray.hide()
            self.server.close()
        except Exception:
            logging.exception("quit cleanup failed")
        self.app.quit()


# ============================================================================
# ENTRY POINT
# ============================================================================


def _print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        pass


def send_to_running(text: str, timeout: int = 400) -> bool:
    """Try to reach a running Orbi over the local socket. Returns True on ok."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout):
        return False
    sock.write((text + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout)
    sock.disconnectFromServer()
    return True


def main() -> int:
    install_logging()
    install_excepthook()
    install_thread_excepthook()

    # DPI awareness must be set before QApplication on Windows for correct
    # coordinates from `foreground_info()`.
    enable_dpi_awareness()

    argv = sys.argv[1:]

    # ---- CLI mode ---------------------------------------------------------
    if argv and argv[0].lower() in CLI_COMMANDS:
        # A QCoreApplication is enough; we don't need a window.
        QCoreApplication(sys.argv)
        if send_to_running(" ".join(argv)):
            return 0
        _print("Orbi isn't running. Start it first (orbi.exe or `python orbi.py`).")
        return 1

    # ---- normal mode ------------------------------------------------------
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORG)

    # If Orbi is already running, wake him and exit.
    if send_to_running("wake"):
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        _print("No system tray available on this system; Orbi will still run.")

    orbi = OrbiApp(app)  # noqa: F841 (keep a reference alive for the app lifetime)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())


# ============================================================================
# BUILD HELPERS
# ============================================================================
#
# These are exposed as strings so you can dump a working PyInstaller spec
# and a Windows build script from a running Orbi (or from the module).

ORBI_SPEC = r"""# orbi.spec -- generated by orbi.py
# Build with:  pyinstaller orbi.spec --clean --noconfirm
# Ship the whole dist/Orbi folder to the target machine.

block_cipher = None

a = Analysis(
    ['orbi.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PySide6.QtNetwork',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.Qt3DCore',
        'PySide6.QtMultimedia',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtTest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Orbi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Orbi',
)
"""


BUILD_BAT = r"""@echo off
setlocal EnableDelayedExpansion
title Building Orbi

echo === Checking for Python ===
where python >nul 2>nul
if errorlevel 1 (
    echo Python not found. Attempting install via winget...
    where winget >nul 2>nul
    if not errorlevel 1 (
        winget install -e --id Python.Python.3.12 --silent ^
            --accept-package-agreements --accept-source-agreements
    ) else (
        echo winget unavailable. Downloading official installer...
        powershell -NoProfile -Command ^
            "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile $env:TEMP\pyinst.exe"
        "%TEMP%\pyinst.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
        del "%TEMP%\pyinst.exe"
    )
    echo.
    echo Python was just installed. Close this window and re-run build_orbi.bat
    echo so your PATH picks it up.
    pause
    exit /b 1
)

echo === Upgrading pip and installing dependencies ===
python -m pip install --upgrade pip
python -m pip install PySide6 psutil pyinstaller
if errorlevel 1 (
    echo Dependency install failed.
    pause
    exit /b 1
)

echo === Building Orbi.exe (one-folder, no console) ===
python -m PyInstaller --noconfirm --clean orbi.spec
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Built: %CD%\dist\Orbi\Orbi.exe
echo.
echo  Ship the WHOLE dist\Orbi folder to the target machine.
echo  On the target: run Orbi.exe once, then
echo    right-click Orbi -^> Settings -^> System -^> check
echo    "Start with Windows".
echo  From then on Windows boots it silently. No Python needed,
echo  no console window, no taskbar entry.
echo ============================================================
pause
"""


README_MD = """# Orbi

A little round desktop robot that keeps you company while you code.

## Features

- Spherical rolling body + floating hemisphere head, drawn entirely in code
- Lives in the system tray. No taskbar entry, no console window
- Right-click Orbi: settings (theme, size, behaviour, reminders, git, hotkey)
- Left-click: boop. Double-click: rubber-duck debugging chat. Drag: pick him up
- Ten themes: classic, retro, hacker, snow, ninja, sunset, ocean, forest, sakura, mono
- Seasonal hats: pumpkin (Halloween), santa (Christmas), party hat (New Year)
- Pomodoro focus timer with focus ring and daily count
- Reminders: water, stretch, eyes, stand, posture
- Git commit awareness + uncommitted-changes nudge
- Battery & CPU awareness (optional, requires `psutil`)
- Achievements: 24 unlockables with persistent progress
- Usage statistics: day streak, top apps, uptime, pomodoro history
- Global hotkey (default: Ctrl+Alt+O) to wake / sleep
- Plugin system: drop .py files in the config folder

## Install (for development)

    python -m pip install PySide6 psutil
    python orbi.py

## Install (for end users)

1. Build the exe on your dev machine: run `build_orbi.bat`.
2. Ship the entire `dist/Orbi` folder to the target machine.
3. Run `Orbi.exe` once, then right-click Orbi -> Settings -> System ->
   check "Start with Windows".

No Python is required on the target machine. Orbi runs silently in the
tray, launches on boot, and never shows a console.

## CLI (from a terminal, when Orbi is running)

    orbi.exe pass
    orbi.exe fail
    orbi.exe build-start
    orbi.exe build-done
    orbi.exe say "hello"
    orbi.exe dnd 45
    orbi.exe sleep
    orbi.exe wake
    orbi.exe quit

Example in a shell pipeline:

    npm test && orbi.exe pass || orbi.exe fail

## Configuration

Everything lives in `%APPDATA%\\Orbi\\`:

- `config.json`   - settings, watched repos, theme, etc.
- `stats.json`    - usage statistics
- `achievements.json` - unlock progress
- `orbi.log`      - rotating log (512 KB, 3 backups)
- `plugins/`      - optional user plugins

## Plugins

A plugin is a `.py` file in `%APPDATA%\\Orbi\\plugins\\` exposing:

    PLUGIN_NAME = "My plugin"

    def poll(app):
        # called on a timer; return a string to make Orbi say it, or None
        return None

    def on_command(cmd, arg, app):
        # return True if you handled the CLI command
        return False
"""


def write_build_files(target_dir: Optional[Path] = None) -> None:
    """Drop orbi.spec, build_orbi.bat, and README.md next to orbi.py."""
    d = Path(target_dir) if target_dir else Path.cwd()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "orbi.spec").write_text(ORBI_SPEC, encoding="utf-8")
        (d / "build_orbi.bat").write_text(BUILD_BAT, encoding="utf-8")
        (d / "README.md").write_text(README_MD, encoding="utf-8")
        logging.info("build files written to %s", d)
    except Exception:
        logging.exception("write_build_files failed")


# === END OF PART 3 ==========================================================