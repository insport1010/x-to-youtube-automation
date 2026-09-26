"""Small wrapper around the existing rclone Google Drive setup."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")

def remote_path(path: str) -> str:
    return f"{REMOTE}:{PurePosixPath(path.strip('/'))}"

def run(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["rclone", *args], check=True, text=True, capture_output=capture)

def list_files(folder: str) -> list[dict]:
    return json.loads(run("lsjson", remote_path(folder), "--files-only").stdout or "[]")

def read_json(path: str, default):
    try:
        return json.loads(run("cat", remote_path(path)).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return default

def write_json(path: str, value) -> None:
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / "state.json"
        local.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        run("copyto", str(local), remote_path(path))

def move(source: str, destination: str) -> None:
    run("moveto", remote_path(source), remote_path(destination), capture=False)

def delete(path: str) -> None:
    run("deletefile", remote_path(path), capture=False)
