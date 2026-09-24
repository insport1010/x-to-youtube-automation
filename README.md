# X to YouTube automation

This GitHub Actions prototype checks `@FConPredict` hourly during the configured local-time window, uploads up to six new videos per day, and publishes them publicly on YouTube.

## GitHub Secrets

Add these secrets:

- `YOUTUBE_CLIENT_SECRET_JSON`: the OAuth desktop-client JSON, as one-line JSON
- `YOUTUBE_TOKEN_JSON`: a refresh-token token file created once by the local OAuth bootstrap

The prototype uses public X profile HTML and `yt-dlp`; it does not use X credentials. X may change its page structure, so this scraper may need maintenance.

## Local OAuth bootstrap

Install dependencies and run:

```text
python -m pip install -r requirements.txt
python auth_bootstrap.py
```

Complete the Google authorization in your browser, then copy the generated `youtube-token.json` contents into the `YOUTUBE_TOKEN_JSON` GitHub Secret.

## Configuration

The workflow uses `Africa/Cairo` by default. Change `TIMEZONE` in the workflow if needed.
