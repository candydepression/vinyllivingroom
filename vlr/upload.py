"""Upload the finished video to YouTube as PRIVATE. Never runs on its own."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .episode import Episode
from .media import VLRError

CONFIG = Path.home() / ".config" / "vinyllivingroom"
SECRET = CONFIG / "client_secret.json"
TOKEN = CONFIG / "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
MUSIC_CATEGORY = "10"


def uploaded_record(ep: Episode) -> dict | None:
    f = ep.out_dir / "uploaded.json"
    return json.loads(f.read_text()) if f.exists() else None


def metadata(ep: Episode) -> dict:
    title = (ep.yt_title or f"{ep.artist} – {ep.album}").strip()
    desc = ep.yt_description.strip()
    problems = []
    if len(title) > 100:
        problems.append(f"YouTube title is {len(title)} characters; the limit is 100.")
    if any(c in title + desc for c in "<>"):
        problems.append("YouTube doesn't allow < or > in the title or description.")
    if len(desc.encode()) > 5000:
        problems.append("YouTube description is longer than 5000 bytes.")
    if len(",".join(ep.yt_tags)) > 500:
        problems.append("Tags add up to more than 500 characters.")
    if problems:
        raise VLRError(" ".join(problems))
    return {
        "snippet": {"title": title, "description": desc, "tags": ep.yt_tags, "categoryId": MUSIC_CATEGORY},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }


def _credentials():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise VLRError("YouTube upload libraries are missing. Run ./setup.sh again.") from None
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not SECRET.exists():
            raise VLRError(
                f"YouTube isn't connected yet. Follow 'Connect YouTube (one time)' in the README, "
                f"then put client_secret.json in {CONFIG}/")
        flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")  # opens your browser to sign in
    CONFIG.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json())
    return creds


def upload(ep: Episode, confirm: bool = False, log=print, on_progress=None) -> str:
    video = ep.final_path
    if not video.exists():
        raise VLRError("There's no finished video yet. Make it first, and watch it.")
    prev = uploaded_record(ep)
    body = metadata(ep)
    if confirm:  # command line: show exactly what will happen and ask
        print(f"\nFile:  {video}\nTitle: {body['snippet']['title']}\nTags:  {', '.join(ep.yt_tags) or '-'}"
              f"\nPrivacy: PRIVATE (you publish it yourself in YouTube Studio)")
        if prev:
            print(f"\n!! Already uploaded on {prev['when']}: {prev['url']}")
        if input("\nType 'upload' to continue: ").strip().lower() != "upload":
            raise VLRError("Cancelled. Nothing was uploaded.")
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=32 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    log("Uploading to YouTube as PRIVATE…")
    resp, retries = None, 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
            retries = 0
            if status and on_progress:
                on_progress(status.progress())
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                time.sleep(2 ** retries)
                continue
            raise VLRError(f"YouTube refused the upload: {e}") from None
    if on_progress:
        on_progress(1.0)
    url = f"https://studio.youtube.com/video/{resp['id']}/edit"
    (ep.out_dir / "uploaded.json").write_text(json.dumps(
        {"id": resp["id"], "url": url, "when": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
    log(f"Uploaded as private. Review and publish it here: {url}")
    return url
