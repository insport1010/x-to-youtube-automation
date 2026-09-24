import json, os, re, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HANDLE = os.getenv("X_HANDLE", "FConPredict")
STATE = Path(os.getenv("STATE_FILE", "state.json"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Africa/Cairo"))
MAX_DAILY = 6
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def state():
    if STATE.exists(): return json.loads(STATE.read_text(encoding="utf-8"))
    return {"uploaded": [], "day": "", "count": 0}

def fetch_candidates():
    url = f"https://x.com/{HANDLE}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    ids = re.findall(r"/status/(\d+)", r.text)
    return list(dict.fromkeys(ids))

def youtube():
    token = json.loads(os.environ["YOUTUBE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(token, SCOPES)
    return build("youtube", "v3", credentials=creds)

def title_for(text, post_id):
    text = re.sub(r"\s+", " ", text or "").strip()
    return (text or f"FConPredict video {post_id}")[:100]

def upload(api, filename, title, description):
    body = {"snippet": {"title": title, "description": description}, "status": {"privacyStatus": "public"}}
    req = api.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(filename, resumable=True))
    return req.execute()["id"]

def main():
    now = datetime.now(TZ)
    if not (12 <= now.hour <= 23): return
    s = state(); today = now.date().isoformat()
    if s.get("day") != today: s = {"uploaded": s.get("uploaded", []), "day": today, "count": 0}
    if s["count"] >= MAX_DAILY: return
    api = youtube()
    for post_id in fetch_candidates():
        if post_id in s["uploaded"] or s["count"] >= MAX_DAILY: continue
        post_url = f"https://x.com/{HANDLE}/status/{post_id}"
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "video.%(ext)s")
            result = subprocess.run(["yt-dlp", "--no-warnings", "--print", "description", "-o", out, post_url], capture_output=True, text=True, timeout=180)
            if result.returncode != 0: continue
            files = list(Path(tmp).glob("video.*"))
            if not files: continue
            description = (result.stdout.strip() + "\n\nSource: " + post_url).strip()
            upload(api, str(files[0]), title_for(result.stdout, post_id), description)
        s["uploaded"].append(post_id); s["count"] += 1
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")

if __name__ == "__main__": main()
