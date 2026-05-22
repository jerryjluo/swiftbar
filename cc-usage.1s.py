#!/usr/bin/env python3
"""SwiftBar plugin: Claude Code usage + live sessions.

Top line: 5-hour usage % and time-to-reset (cached 5 min).
Dropdown: live Claude Code sessions sorted by age. Click opens the tmux pane.
Invoke with `--open <tmux-target>` to deep-link into a pane.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

os.environ["PATH"] = (
    "/Users/jerryluo/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
)

SESSIONS_DIR = Path.home() / ".claude" / "sessions"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
CACHE_DIR = Path.home() / ".cache" / "swiftbar"
USAGE_CACHE = CACHE_DIR / "cc-usage.json"
USAGE_TTL = 5 * 60
CLAUDE_USAGE_SCRIPT = Path.home() / "scripts" / "claude-usage.py"
CCUSAGE_CHART_SCRIPT = Path.home() / "scripts" / "ccusage-chart.py"
WEZTERM_BUNDLE = "com.github.wez.wezterm"
SCRIPT_PATH = Path(__file__).resolve()

_TITLE_CACHE: dict[str, tuple[float, str]] = {}


@dataclass
class Session:
    pid: int
    session_id: str
    cwd: str
    status: str
    started_at_ms: int
    updated_at_ms: int
    tmux_target: str
    title: str = ""

    @property
    def age_seconds(self) -> float:
        ref = self.updated_at_ms or self.started_at_ms
        return max(0.0, time.time() - ref / 1000.0)

    @property
    def cwd_short(self) -> str:
        home = str(Path.home())
        path = "~" + self.cwd[len(home):] if self.cwd.startswith(home) else self.cwd
        parts = path.split("/")
        if len(parts) <= 2:
            return path
        abbreviated = [parts[0]] + [_abbrev(p) for p in parts[1:-1]] + [parts[-1]]
        return "/".join(abbreviated)


def _abbrev(segment: str) -> str:
    if not segment:
        return segment
    if segment.startswith(".") and len(segment) > 1:
        return segment[:2]
    return segment[:1]


# --- Session discovery (mirrors claude-session-tui.py) ---

def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def pid_tty(pid: int) -> str | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    tty = out.stdout.strip()
    if not tty or tty == "??":
        return None
    return tty


def tmux_panes() -> dict[str, str]:
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_tty}|#{session_name}:#{window_index}.#{pane_index}"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}
    panes: dict[str, str] = {}
    for line in out.stdout.splitlines():
        tty, _, target = line.partition("|")
        if tty.startswith("/dev/"):
            tty = tty[len("/dev/"):]
        if tty and target:
            panes[tty] = target
    return panes


def session_title(session_id: str) -> str:
    if not session_id:
        return ""
    matches = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl")) if PROJECTS_DIR.is_dir() else []
    if not matches:
        return _TITLE_CACHE.get(session_id, (0.0, ""))[1]
    jsonl = matches[0]
    try:
        mtime = jsonl.stat().st_mtime
    except OSError:
        return _TITLE_CACHE.get(session_id, (0.0, ""))[1]
    cached = _TITLE_CACHE.get(session_id)
    if cached and cached[0] == mtime:
        return cached[1]
    title = ""
    try:
        with jsonl.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"ai-title"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "ai-title":
                    candidate = d.get("aiTitle")
                    if isinstance(candidate, str) and candidate:
                        title = candidate
    except OSError:
        pass
    _TITLE_CACHE[session_id] = (mtime, title)
    return title


def load_sessions() -> list[Session]:
    if not SESSIONS_DIR.is_dir():
        return []
    pane_map = tmux_panes()
    if not pane_map:
        return []
    sessions: list[Session] = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int) or not pid_alive(pid):
            continue
        if data.get("kind") != "interactive":
            continue
        tty = pid_tty(pid)
        if not tty:
            continue
        target = pane_map.get(tty)
        if not target:
            continue
        session_id = str(data.get("sessionId", ""))
        sessions.append(Session(
            pid=pid,
            session_id=session_id,
            cwd=str(data.get("cwd", "")),
            status=str(data.get("status", "")),
            started_at_ms=int(data.get("startedAt", 0)),
            updated_at_ms=int(data.get("updatedAt", 0)),
            tmux_target=target,
            title=session_title(session_id),
        ))
    return sessions


# --- Anthropic usage cache (5-min TTL) ---

def get_usage() -> dict | None:
    try:
        st = USAGE_CACHE.stat()
        if time.time() - st.st_mtime < USAGE_TTL:
            return json.loads(USAGE_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    try:
        out = subprocess.run(
            ["uv", "run", str(CLAUDE_USAGE_SCRIPT), "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            data = json.loads(out.stdout)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            USAGE_CACHE.write_text(json.dumps(data))
            return data
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        return json.loads(USAGE_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def format_usage(data: dict | None) -> str:
    if not data:
        return "CC: ?"
    fh = data.get("five_hour") or {}
    pct = fh.get("utilization")
    resets = fh.get("resets_at")
    if pct is None:
        return "CC: ?"
    if resets:
        try:
            dt = datetime.fromisoformat(resets.replace("Z", "+00:00"))
            secs = int((dt - datetime.now(timezone.utc)).total_seconds())
            if secs < 0:
                rel = "0m"
            elif secs < 3600:
                rel = f"{secs // 60}m"
            else:
                rel = f"{secs // 3600}h{(secs % 3600) // 60}m"
        except ValueError:
            rel = "—"
    else:
        rel = "—"
    extra = data.get("extra_usage") or {}
    extra_str = ""
    if extra.get("is_enabled"):
        used = (extra.get("used_credits") or 0) / 100
        cur = extra.get("currency", "USD")
        sym = "$" if cur == "USD" else f" {cur}"
        extra_str = f" · {sym}{used:.2f}"
    return f"CC {pct:.0f}% · {rel}{extra_str}"


# --- Formatting ---

def format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{int(seconds / 86400)}d"


def status_dot(status: str) -> str:
    if status == "busy":
        return "🟠"
    if status == "idle":
        return "🟢"
    return "⚪"


# --- Click handler ---

def deep_link(target: str) -> None:
    subprocess.run(
        ["osascript", "-e", f'tell application id "{WEZTERM_BUNDLE}" to activate'],
        capture_output=True, timeout=2,
    )
    session, _, rest = target.partition(":")
    window, _, pane = rest.partition(".")
    subprocess.run(["tmux", "switch-client", "-t", session], capture_output=True, timeout=2)
    subprocess.run(["tmux", "select-window", "-t", f"{session}:{window}"], capture_output=True, timeout=2)
    subprocess.run(["tmux", "select-pane", "-t", f"{session}:{window}.{pane}"], capture_output=True, timeout=2)


# --- Render ---

def render() -> None:
    print(format_usage(get_usage()))
    print("---")
    print(
        f'📊 ccusage chart | shell="/Users/jerryluo/.local/bin/uv" '
        f'param1="run" param2="{CCUSAGE_CHART_SCRIPT}" '
        f'terminal=false refresh=false'
    )
    print("---")
    sessions = sorted(load_sessions(), key=lambda s: s.age_seconds)
    if not sessions:
        print("No live sessions | color=gray")
        return
    for s in sessions:
        dot = status_dot(s.status)
        age = format_age(s.age_seconds)
        title = s.title or "—"
        label = f"{dot} {age}  {s.cwd_short}  ·  {title}"
        params = (
            f'shell="{SCRIPT_PATH}" '
            f'param1="--open" '
            f'param2="{s.tmux_target}" '
            f'terminal=false refresh=false font=Menlo'
        )
        print(f"{label} | {params}")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--open":
        deep_link(sys.argv[2])
        return
    render()


if __name__ == "__main__":
    main()
