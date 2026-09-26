"""Move one queued Drive video to Rafa and remove it 24 hours later."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath, PureWindowsPath
from zoneinfo import ZoneInfo

from cloud_drive import delete, list_files, move, read_json, write_json

MAIN = "Rafa"
QUEUE = "Rafa/Unused"
AUTO_QUEUE = "Rafa/Unused/auto"
STATE_PATH = "Rafa/.automation/released_videos.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
CAIRO = ZoneInfo("Africa/Cairo")
RELEASE_HOURS = {13, 17, 19, 23}

def videos(folder: str) -> list[dict]:
    return [x for x in list_files(folder) if PurePosixPath(x["Name"]).suffix.lower() in VIDEO_EXTENSIONS]

def main() -> None:
    if os.environ.get("FORCE_RUN") != "1" and datetime.now(CAIRO).hour not in RELEASE_HOURS:
        return
    now = datetime.now(timezone.utc)
    state = read_json(STATE_PATH, {"releases": []})
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

    # Automatically downloaded videos have priority. Within each queue, oldest first.
    auto = videos(AUTO_QUEUE)
    manual = videos(QUEUE)
    queued = [(AUTO_QUEUE, x) for x in auto] or [(QUEUE, x) for x in manual]
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

    write_json(STATE_PATH, {"releases": retained})

if __name__ == "__main__":
    main()
