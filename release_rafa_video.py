"""Move one queued Drive video to Rafa and remove it 24 hours later."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath, PureWindowsPath
from zoneinfo import ZoneInfo

from cloud_drive import delete, list_files, move, read_json, write_json

MAIN = "Rafa"
QUEUE = "Rafa/Unused"
STATE_PATH = "Rafa/.automation/released_videos.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
CAIRO = ZoneInfo("Africa/Cairo")
MIN_RELEASE_INTERVAL = timedelta(minutes=60)

def videos(folder: str) -> list[dict]:
    try:
        files = list_files(folder)
    except subprocess.CalledProcessError:
        # The automatic queue is optional; a missing folder means it is empty.
        return []
    return [
        x for x in files
        if PurePosixPath(x["Name"]).suffix.lower() in VIDEO_EXTENSIONS
        or str(x.get("MimeType", "")).lower().startswith("video/")
    ]

def main() -> None:
    now = datetime.now(timezone.utc)
    state = read_json(STATE_PATH, {"releases": []})
    last_release_at = state.get("last_release_at")
    if last_release_at:
        try:
            if now - datetime.fromisoformat(last_release_at) < MIN_RELEASE_INTERVAL:
                return
        except (TypeError, ValueError):
            pass
    retained = []
    main_names = {x["Name"] for x in list_files(MAIN)}

    for release in state.get("releases", []):
        try:
            moved_at = datetime.fromisoformat(release["moved_at"])
            name = release.get("name") or PureWindowsPath(release["path"]).name
        except (KeyError, TypeError, ValueError):
            retained.append(release); continue
        if now - moved_at < timedelta(hours=24):
            retained.append(release)
        elif name in main_names and PurePosixPath(name).suffix.lower() in VIDEO_EXTENSIONS:
            delete(f"{MAIN}/{name}")

    # Automatic X downloads have priority; manual additions are the fallback.
    auto_names = {x.get("name") for x in read_json("Rafa/.automation/downloaded_videos.json", {"videos": []}).get("videos", [])}
    queued_files = videos(QUEUE)
    auto = [x for x in queued_files if x["Name"] in auto_names]
    manual = [x for x in queued_files if x["Name"] not in auto_names]
    queued = [(QUEUE, x) for x in (auto or manual)]
    queued.sort(key=lambda pair: (pair[1].get("ModTime", ""), pair[1]["Name"].lower()))
    if queued:
        source_queue, item = queued[0]
        source_name = item["Name"]
        destination_name = source_name
        stem, suffix = PurePosixPath(source_name).stem, PurePosixPath(source_name).suffix
        index = 2
        while destination_name in main_names:
            destination_name = f"{stem} ({index}){suffix}"; index += 1
        move(f"{source_queue}/{source_name}", f"{MAIN}/{destination_name}")
        retained.append({"name": destination_name, "moved_at": now.isoformat()})

    output_state = {"releases": retained}
    if queued:
        output_state["last_release_at"] = now.isoformat()
    elif last_release_at:
        output_state["last_release_at"] = last_release_at
    write_json(STATE_PATH, output_state)

if __name__ == "__main__":
    main()
