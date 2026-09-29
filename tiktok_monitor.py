"""Monitor a public TikTok profile for new videos using yt-dlp metadata."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "tiktok_monitor_data"
ACCOUNTS_FILE = ROOT / "tiktok_accounts.txt"
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", r"H:\My Drive\Rafa\Unused"))
MANIFEST = Path(os.getenv("DOWNLOAD_MANIFEST", r"H:\My Drive\Rafa\.automation\downloaded_videos.json"))
REPORT_EVENTS_FILE = DATA_DIR / "report_events.jsonl"
WEBSITE_DOWNLOAD_LIMIT_BYTES = 100 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_username(value: str) -> str:
    username = value.strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{2,24}", username):
        raise ValueError("Invalid TikTok username")
    return username


def load_accounts(path: Path) -> list[str]:
    if not path.exists():
        raise RuntimeError(f"Account list is missing: {path}")
    accounts = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        username = clean_username(line)
        if username not in accounts:
            accounts.append(username)
    if not accounts:
        raise RuntimeError("accounts.txt does not contain any TikTok usernames")
    return accounts


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {str(item) for item in payload.get("seen_video_ids", [])}
    except (OSError, ValueError, TypeError):
        return set()


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def append_line(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(text.rstrip() + "\n")


def report_event(username: str, video: dict | None, result: str, error: str = "") -> None:
    payload = {
        "timestamp": utc_now(),
        "account": username,
        "video": (video or {}).get("id") or (video or {}).get("url") or "-",
        "result": result,
        "error": error.replace("\n", " ")[:300],
    }
    append_line(REPORT_EVENTS_FILE, json.dumps(payload, ensure_ascii=False))


def sanitize_filename(text: str, fallback: str = "video") -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).rstrip(" .")
    # Leave ample room for the destination path, the temporary ".part"
    # suffix, duplicate counters, and multi-unit Unicode characters on
    # Windows/Google Drive.
    text = text or fallback
    return (text[:99] if len(text) > 100 else text).rstrip(" .")


def choose_output_path(caption: str) -> Path:
    base = sanitize_filename(caption, fallback="video")
    path = DOWNLOAD_DIR / f"{base}.mp4"
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = DOWNLOAD_DIR / f"{base} ({index}).mp4"
        if not candidate.exists():
            return candidate
        index += 1


def hidden_process_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def fetch_recent(username: str, count: int) -> list[dict]:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is not installed or is not on PATH")
    command = [
        executable,
        "--flat-playlist",
        "--playlist-end",
        str(count),
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        f"https://www.tiktok.com/@{username}",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        creationflags=hidden_process_flags(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"yt-dlp exited with {result.returncode}")

    payload = json.loads(result.stdout)
    videos: list[dict] = []
    for entry in payload.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        video_id = str(entry["id"])
        videos.append(
            {
                "id": video_id,
                "description": entry.get("description") or entry.get("title") or "",
                "timestamp": entry.get("timestamp"),
                "url": entry.get("url") or f"https://www.tiktok.com/@{username}/video/{video_id}",
                "view_count": entry.get("view_count"),
                "like_count": entry.get("like_count"),
                "comment_count": entry.get("comment_count"),
                "share_count": entry.get("repost_count"),
                "sec_uid": entry.get("channel_id"),
            }
        )
    if not videos:
        raise RuntimeError("TikTok returned no videos")
    return videos


def download_video(video: dict) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = choose_output_path(video.get("description", ""))
    errors = []
    for attempt in range(1, 6):
        try:
            download_via_tikdownloader(video["url"], output_path)
            MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"videos": []}
            manifest.setdefault("videos", []).append({
                "name": output_path.name,
                "post_id": f"tiktok:{video['id']}",
                "source": "tiktok",
                "downloaded_at": utc_now(),
            })
            write_json(MANIFEST, manifest)
            return output_path
        except Exception as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            if attempt < 5:
                time.sleep(5)
    raise RuntimeError("TikDownloader MP4 HD failed after 5 attempts: " + " | ".join(errors))


def download_via_tikdownloader(video_url: str, output_path: Path) -> None:
    profile_dir = DATA_DIR / "browser_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_suffix(output_path.suffix + ".part")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
            args=[
                "--window-position=-32000,-32000",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-notifications",
            ],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            def close_unexpected_page(opened_page):
                if opened_page != page:
                    try:
                        opened_page.close()
                    except Exception:
                        pass

            context.on("page", close_unexpected_page)
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page.goto(
                "https://tikdownloader.io/en",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            search = page.get_by_role("textbox", name="Search")
            search.wait_for(state="visible", timeout=30_000)
            search.fill(video_url)
            page.get_by_role("button", name="Download").click()

            hd_link = page.get_by_role("link", name="Download MP4 HD")
            hd_link.wait_for(state="visible", timeout=30_000)
            media_url = hd_link.get_attribute("href")

            if not media_url:
                raise RuntimeError("TikDownloader did not return an MP4 link")

            def download_regular_mp4() -> None:
                regular_link = page.get_by_role(
                    "link", name="Download MP4 [1]", exact=True
                ).first
                try:
                    regular_link.wait_for(state="visible", timeout=10_000)
                except Exception:
                    regular_link = page.get_by_role(
                        "link", name="Download MP4 [2]", exact=True
                    ).first
                    regular_link.wait_for(state="visible", timeout=10_000)
                regular_url = regular_link.get_attribute("href")
                if not regular_url:
                    raise RuntimeError("TikDownloader did not return a regular MP4 link")
                with requests.get(
                    regular_url,
                    headers={
                        "Referer": "https://tikdownloader.io/",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36"
                        ),
                    },
                    stream=True,
                    timeout=120,
                ) as regular_response:
                    regular_response.raise_for_status()
                    with part_path.open("wb") as stream:
                        for chunk in regular_response.iter_content(
                            chunk_size=256 * 1024
                        ):
                            if chunk:
                                stream.write(chunk)

            # Inspect the website's MP4 HD response before choosing a downloader.
            try:
                response = requests.get(
                    media_url,
                    headers={
                        "Referer": "https://tikdownloader.io/",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36"
                        ),
                    },
                    stream=True,
                    timeout=120,
                )
                response.raise_for_status()
            except Exception as request_error:
                # Reuse the browser session if the standalone request is
                # rejected. This still avoids the ad-triggering HD button and
                # gives us the exact body size before selecting the final path.
                try:
                    browser_response = context.request.get(
                        media_url,
                        headers={"Referer": "https://tikdownloader.io/"},
                        timeout=120_000,
                    )
                    if not browser_response.ok:
                        raise RuntimeError(
                            f"browser MP4 request returned HTTP {browser_response.status}"
                        )
                    browser_body = browser_response.body()
                    if len(browser_body) > WEBSITE_DOWNLOAD_LIMIT_BYTES:
                        download_regular_mp4()
                    else:
                        part_path.write_bytes(browser_body)
                except Exception as browser_error:
                    raise RuntimeError(
                        f"Direct HD request failed ({request_error}); "
                        f"browser MP4 request failed ({browser_error})"
                    ) from browser_error
            else:
                with response:
                    size_header = response.headers.get("Content-Length")
                    try:
                        expected_size = int(size_header) if size_header else None
                    except (TypeError, ValueError):
                        expected_size = None

                    if (
                        expected_size is not None
                        and expected_size > WEBSITE_DOWNLOAD_LIMIT_BYTES
                    ):
                        download_regular_mp4()
                    else:
                        downloaded_size = 0
                        switched_from_hd = False
                        with part_path.open("wb") as stream:
                            for chunk in response.iter_content(chunk_size=256 * 1024):
                                if not chunk:
                                    continue
                                stream.write(chunk)
                                downloaded_size += len(chunk)
                                if (
                                    expected_size is None
                                    and downloaded_size > WEBSITE_DOWNLOAD_LIMIT_BYTES
                                ):
                                    switched_from_hd = True
                                    break
                        if switched_from_hd:
                            download_regular_mp4()
            if part_path.stat().st_size == 0:
                raise RuntimeError("Downloaded video was empty")
            part_path.replace(output_path)
        finally:
            context.close()


def download_direct_from_profile_api(video: dict, output_path: Path) -> None:
    sec_uid = video.get("sec_uid")
    if not sec_uid:
        raise RuntimeError("Missing TikTok secondary user ID")
    params = {
        "aid": "1988",
        "app_language": "en",
        "app_name": "tiktok_web",
        "browser_language": "en-US",
        "browser_name": "Mozilla",
        "browser_online": "true",
        "browser_platform": "Win32",
        "browser_version": "5.0 (Windows)",
        "channel": "tiktok_web",
        "cookie_enabled": "true",
        "count": "15",
        "cursor": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        "device_id": str(random.randint(7250000000000000000, 7325099899999994577)),
        "device_platform": "web_pc",
        "focus_state": "true",
        "from_page": "user",
        "history_len": "2",
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "language": "en",
        "os": "windows",
        "priority_region": "",
        "referer": "",
        "region": "US",
        "screen_height": "1080",
        "screen_width": "1920",
        "secUid": sec_uid,
        "type": "1",
        "tz_name": "UTC",
        "verifyFp": f"verify_{random.randrange(16**7):07x}",
        "webcast_language": "en",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.tiktok.com/",
    }
    session = requests.Session()
    session.headers.update(headers)
    response = session.get(
        "https://www.tiktok.com/api/creator/item_list/", params=params, timeout=60
    )
    response.raise_for_status()
    items = response.json().get("itemList") or []
    item = next((entry for entry in items if str(entry.get("id")) == video["id"]), None)
    if not item:
        raise RuntimeError("New video was not returned by TikTok's profile endpoint")
    video_data = item.get("video") or {}
    structured_urls = (video_data.get("PlayAddrStruct") or {}).get("UrlList") or []
    urls = structured_urls or video_data.get("playAddr") or video_data.get("downloadAddr") or []
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        raise RuntimeError("TikTok did not provide an MP4 URL")

    part_path = output_path.with_suffix(output_path.suffix + ".part")
    preferred_url = next((url for url in reversed(urls) if "www.tiktok.com/aweme/v1/play/" in url), urls[0])
    session.headers["Referer"] = video["url"]
    with session.get(preferred_url, stream=True, timeout=180) as media:
        media.raise_for_status()
        with part_path.open("wb") as stream:
            for chunk in media.iter_content(chunk_size=256 * 1024):
                if chunk:
                    stream.write(chunk)
    if part_path.stat().st_size == 0:
        raise RuntimeError("Downloaded video was empty")
    part_path.replace(output_path)


def run_once(username: str, count: int) -> int:
    DATA_DIR.mkdir(exist_ok=True)
    state_path = DATA_DIR / f"{username}_seen.json"
    event_path = DATA_DIR / f"{username}_new_videos.jsonl"
    activity_path = DATA_DIR / f"{username}_activity.log"
    seen = load_seen(state_path)

    try:
        videos = fetch_recent(username, count)
    except Exception as exc:
        message = f"[{utc_now()}] Check failed: {type(exc).__name__}: {exc}"
        append_line(activity_path, message)
        report_event(username, None, "check_failed", f"{type(exc).__name__}: {exc}")
        return 1

    current_ids = {video["id"] for video in videos}
    if not state_path.exists():
        newest_id = videos[0]["id"]
        seen.update(current_ids - {newest_id})
        append_line(
            activity_path,
            f"[{utc_now()}] Baseline saved; newest video will be downloaded.",
        )

    new_videos = [video for video in videos if video["id"] not in seen]
    for video in reversed(new_videos):
        try:
            saved_path = download_video(video)
        except Exception as exc:
            append_line(
                activity_path,
                f"[{utc_now()}] Download failed for {video['url']}: {type(exc).__name__}: {exc}",
            )
            report_event(username, video, "failed", f"{type(exc).__name__}: {exc}")
            continue
        event = {
            "detected_at": utc_now(),
            "username": username,
            "saved_as": str(saved_path),
            **video,
        }
        append_line(event_path, json.dumps(event, ensure_ascii=False))
        append_line(activity_path, f"[{event['detected_at']}] DOWNLOADED: {saved_path}")
        report_event(username, video, "downloaded")
        seen.add(video["id"])

    if not new_videos:
        report_event(username, None, "no_new")

    write_json(state_path, {"username": username, "updated_at": utc_now(), "seen_video_ids": sorted(seen)})
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor public TikTok accounts for new videos")
    parser.add_argument("--username", help="Monitor one account instead of accounts.txt")
    parser.add_argument("--accounts-file", type=Path, default=ACCOUNTS_FILE)
    parser.add_argument("--count", type=int, default=10)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        usernames = [clean_username(args.username)] if args.username else load_accounts(args.accounts_file)
    except Exception as exc:
        DATA_DIR.mkdir(exist_ok=True)
        append_line(DATA_DIR / "monitor_errors.log", f"[{utc_now()}] {type(exc).__name__}: {exc}")
        raise SystemExit(1)
    result = 0
    for username in usernames:
        result = max(result, run_once(username, max(1, min(args.count, 30))))
    raise SystemExit(result)
