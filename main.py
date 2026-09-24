import json, os, re, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
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
    r = requests.get(f"https://x.com/{HANDLE}", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return list(dict.fromkeys(re.findall(r"/status/(\d+)", r.text)))

def youtube():
    token = json.loads(os.environ["YOUTUBE_TOKEN_JSON"])
    return build("youtube", "v3", credentials=Credentials.from_authorized_user_info(token, SCOPES))

def title_for(text, post_id):
    text = re.sub(r"\s+", " ", text or "").strip()
    return (text or f"FConPredict video {post_id}")[:100]

def upload(api, filename, title, description):
    body = {"snippet": {"title": title, "description": description}, "status": {"privacyStatus": "public"}}
    return api.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(filename, resumable=True)).execute()["id"]

def qualifies_as_short(filename):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height", "-of", "json", filename], capture_output=True, text=True)
    if p.returncode != 0: return False
    d = json.loads(p.stdout or "{}")
    duration = float(d.get("format", {}).get("duration", 0) or 0)
    video = next((x for x in d.get("streams", []) if x.get("width") and x.get("height")), None)
    return bool(video and duration <= 180 and video["height"] >= video["width"])

def main():
    now = datetime.now(TZ)
    if not (12 <= now.hour <= 23) and os.getenv("ALLOW_OUT_OF_WINDOW") != "1": return
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
            files = list(Path(tmp).glob("video.*")) if result.returncode == 0 else []
            if not files or not qualifies_as_short(str(files[0])): continue
            upload(api, str(files[0]), title_for(result.stdout, post_id), (result.stdout.strip() + "\n\nSource: " + post_url).strip())
        s["uploaded"].append(post_id); s["count"] += 1
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")

if __name__ == "__main__": main()
