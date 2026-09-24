import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
client = json.loads(input("Paste the OAuth desktop-client JSON: "))
flow = InstalledAppFlow.from_client_config(client, {"installed": SCOPES})
creds = flow.run_local_server(port=0)
Path("youtube-token.json").write_text(creds.to_json(), encoding="utf-8")
print("Created youtube-token.json. Store its contents as GitHub Secret YOUTUBE_TOKEN_JSON.")
