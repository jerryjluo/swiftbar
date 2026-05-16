#!/usr/bin/env python3
"""SwiftBar plugin: device IP + listening ports on all interfaces.

Menu bar shows the device IPv4. Dropdown lists port + process for each
IPv4 TCP socket bound to 0.0.0.0 / *, in monospace, refreshed every 10s.
"""

from __future__ import annotations

import os
import re
import subprocess

os.environ["PATH"] = (
    "/Users/jerryluo/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def device_ip() -> str:
    for iface in ("en0", "en1"):
        try:
            out = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if out:
                return out
        except Exception:
            pass
    return "?.?.?.?"


def process_name(pid: str) -> str:
    try:
        out = subprocess.run(
            ["ps", "-p", pid, "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if out:
            return os.path.basename(out)
    except Exception:
        pass
    return ""


def process_command(pid: str) -> str:
    try:
        return subprocess.run(
            ["ps", "-p", pid, "-o", "command="],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except Exception:
        return ""


def process_cwd(pid: str) -> str:
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except Exception:
        return ""
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def shorten_path(path: str) -> str:
    if not path:
        return ""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~/" + path[len(home) + 1 :]
    return path


def script_label(comm: str, pid: str) -> str:
    """For node/python procs, return '<comm> <script> (<cwd>)'."""
    base = os.path.basename(comm) if comm else ""
    is_script_runner = base in {"node", "python", "python3", "bun", "deno", "ruby"} or \
        base.startswith("python")
    if not is_script_runner:
        return base or comm

    cmd = process_command(pid)
    script = ""
    if cmd:
        parts = cmd.split()
        # Options that take a value we should NOT treat as the script.
        value_opts = {"-e", "--eval", "-p", "--print", "-c", "-m"}
        for tok in parts[1:]:
            if tok in value_opts:
                script = "(inline)"
                break
            if tok.startswith("-"):
                continue
            script = shorten_path(tok)
            break

    cwd = shorten_path(process_cwd(pid))
    label = base
    if script:
        label += f" {script}"
    if cwd:
        label += f" · {cwd}"
    return label


def docker_port_map() -> dict[int, str]:
    """Map host port -> container name for running docker/orbstack containers."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Ports}}"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return {}

    mapping: dict[int, str] = {}
    for line in out.splitlines():
        name, _, ports = line.partition("|")
        if not name:
            continue
        for m in re.finditer(r"(?:0\.0\.0\.0|\[::\]):(\d+)->", ports):
            mapping.setdefault(int(m.group(1)), name)
    return mapping


def listening_rows() -> list[tuple[int, str, str]]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return []

    rows: dict[tuple[int, str], str] = {}
    for line in result.stdout.splitlines():
        if "IPv4" not in line:
            continue
        m = re.search(r"(?:\*|0\.0\.0\.0):(\d+)\s+\(LISTEN\)", line)
        if not m:
            continue
        port = int(m.group(1))
        parts = line.split()
        if len(parts) < 2:
            continue
        proc = parts[0]
        pid = parts[1]
        comm = process_name(pid) or proc
        full = script_label(comm, pid)
        key = (port, full)
        rows[key] = full

    return sorted([(p, n, n) for (p, n) in rows.keys()])


def main() -> None:
    ip = device_ip()
    rows = listening_rows()
    containers = docker_port_map()
    annotated = []
    for port, name, _ in rows:
        label = name
        if "orbstack" in name.lower() and port in containers:
            label = f"{name} · {containers[port]}"
        annotated.append((port, label))
    rows = [(p, n, n) for (p, n) in annotated]

    print(ip)
    print("---")
    style = "font=Menlo size=13 color=#ffffff,#000000"
    row_style = f"{style} shell=/bin/sh param1=-c param2='exit 0' terminal=false refresh=false"
    if not rows:
        print(f"No listening ports | {style}")
        return

    width = max(len(str(p)) for p, _, _ in rows)
    print(f"{'PORT'.ljust(width)}  PROCESS | {style}")
    print("---")
    for port, name, _ in rows:
        line = f"{str(port).ljust(width)}  {name}"
        print(f"{line} | {row_style}")


if __name__ == "__main__":
    main()
