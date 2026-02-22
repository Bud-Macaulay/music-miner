from fastapi import FastAPI, BackgroundTasks, HTTPException, WebSocket, Query
from fastapi.responses import RedirectResponse
from yt_dlp import YoutubeDL
from pathlib import Path
import threading
import asyncio
import copy

app = FastAPI()

DOWNLOAD_DIR = Path("/downloads")
ARCHIVE_FILE = Path("/download_archive.txt")

DOWNLOAD_DIR.mkdir(exist_ok=True)

tasks_lock = threading.Lock()

# Each task is a dict: {"url": ..., "title": ..., "state": ...}
download_tasks = []


def extract_playlist(url: str):
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

        if "entries" not in info:
            raise ValueError("Not a playlist")

        videos = []
        for entry in info["entries"]:
            if not entry:
                continue

            video_url = entry.get("url")
            if not video_url:
                continue

            videos.append(
                {
                    "url": video_url,
                    "title": entry.get("title", "Unknown"),
                    "state": "queued",
                }
            )
        return videos


def extract_single_video(url: str):
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

        return [
            {
                "url": info.get("webpage_url") or url,
                "title": info.get("title", "Unknown"),
                "state": "queued",
            }
        ]


def download_video(task, quality):
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
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([task["url"]])

        with tasks_lock:
            task["state"] = "finished"

    except Exception:
        with tasks_lock:
            task["state"] = "error"


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/add")
async def add(
    background_tasks: BackgroundTasks,
    playlist: str | None = Query(None),
    video: str | None = Query(None),
    quality: str = "low",
):
    if not playlist and not video:
        raise HTTPException(400, "Provide either playlist or video parameter")

    try:
        if playlist:
            videos = extract_playlist(playlist)
        else:
            videos = extract_single_video(video)

    except Exception:
        raise HTTPException(400, "Failed to extract media info")

    # Prevent duplicates
    with tasks_lock:
        existing_urls = {t["url"] for t in download_tasks}
        new_videos = [v for v in videos if v["url"] not in existing_urls]
        download_tasks.extend(new_videos)

    for task in new_videos:
        background_tasks.add_task(download_video, task, quality)

    return {
        "status": "queued",
        "count": len(new_videos),
        "titles": [v["title"] for v in new_videos],
    }


@app.get("/queue")
async def list_queue(state: str = None, skip: int = 0, limit: int = 50):
    with tasks_lock:
        tasks = download_tasks
        if state:
            tasks = [t for t in download_tasks if t["state"] == state]

        return {"tasks": tasks[skip : skip + limit]}


@app.websocket("/ws/queue")
async def websocket_queue(ws: WebSocket, refresh: float = 2):
    refresh = max(refresh, 0.25)
    await ws.accept()
    try:
        while True:
            with tasks_lock:
                snapshot = copy.deepcopy(download_tasks)
            await ws.send_json(snapshot)
            await asyncio.sleep(refresh)

    except Exception:
        await ws.close()


# for dev usage - uvicorn on port
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8250, reload=True)
