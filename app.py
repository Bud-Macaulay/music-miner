from fastapi import FastAPI, BackgroundTasks, HTTPException
from yt_dlp import YoutubeDL
from pathlib import Path
from fastapi import WebSocket
import threading
import asyncio

app = FastAPI()

DOWNLOAD_DIR = Path("./downloads")
ARCHIVE_FILE = Path("./download_archive.txt")

DOWNLOAD_DIR.mkdir(exist_ok=True)

lock = threading.Lock()
tasks_lock = threading.Lock()

# Each task is a dict: {"url": ..., "title": ..., "state": ...}
download_tasks = []


def extract_videos(playlist_url):
    """
    Returns a list of dicts for each video in the playlist.
    """
    ydl_opts = {"quiet": True, "extract_flat": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        videos = []
        for entry in info.get("entries", []):
            if entry is None:
                continue
            videos.append(
                {
                    "url": entry["url"],
                    "title": entry.get("title", "Unknown"),
                    "state": "queued",
                }
            )
        return videos


def download_video(task, quality):
    """
    Downloads a single video as MP3 and updates state.
    """
    with tasks_lock:
        task["state"] = "downloading"

    quality_map = {"low": "128", "medium": "192", "high": "320"}
    bitrate = quality_map.get(quality, "192")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "download_archive": str(ARCHIVE_FILE),
        "ignoreerrors": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate,
            }
        ],
        "embedmetadata": False,
        "embedthumbnail": False,
        "writethumbnail": False,
    }

    try:
        with lock:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([task["url"]])
        with tasks_lock:
            task["state"] = "finished"
    except Exception:
        with tasks_lock:
            task["state"] = "error"


@app.get("/add")
async def add_playlist(
    playlistURL: str, quality: str = "low", background_tasks: BackgroundTasks = None
):
    if not playlistURL:
        raise HTTPException(status_code=400, detail="playlistURL required")

    # Extract all videos first
    try:
        videos = extract_videos(playlistURL)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to extract playlist info")

    with tasks_lock:
        download_tasks.extend(videos)

    # Queue each video separately
    for video in videos:
        background_tasks.add_task(download_video, video, quality)

    return {"status": "queued", "videos": [v["title"] for v in videos]}


@app.get("/queue")
async def list_queue(state: str = None, skip: int = 0, limit: int = 50):
    with tasks_lock:
        tasks = download_tasks
        if state:
            tasks = [t for t in download_tasks if t["state"] == state]
        return {"tasks": tasks[skip : skip + limit]}


@app.websocket("/ws/queue")
async def websocket_queue(ws: WebSocket, refresh: float = 2):
    # enforce minimum refresh interval
    refresh = max(refresh, 0.25)
    await ws.accept()
    try:
        while True:
            with tasks_lock:
                snapshot = download_tasks.copy()
            await ws.send_json(snapshot)
            await asyncio.sleep(refresh)
    except Exception:
        await ws.close()
