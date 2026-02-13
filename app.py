from fastapi import FastAPI, BackgroundTasks, HTTPException
from yt_dlp import YoutubeDL
from pathlib import Path
import threading

app = FastAPI()

DOWNLOAD_DIR = Path("./downloads")
ARCHIVE_FILE = Path("./download_archive.txt")

DOWNLOAD_DIR.mkdir(exist_ok=True)

lock = threading.Lock()  # prevents concurrent archive corruption


def download_playlist(playlist_url: str, quality: str):
    """
    Downloads playlist as MP3 using yt-dlp.
    Uses archive file to avoid re-downloading videos.
    """

    quality_map = {"low": "128", "medium": "192", "high": "320"}

    bitrate = quality_map.get(quality, "192")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        # Prevent duplicate downloads
        "download_archive": str(ARCHIVE_FILE),
        "ignoreerrors": True,
        # Extract audio
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate,
            }
        ],
        # Metadata
        "embedmetadata": False,
        "embedthumbnail": False,
        "writethumbnail": False,
    }

    with lock:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([playlist_url])


@app.get("/add")
async def add_playlist(
    playlistURL: str, quality: str = "medium", background_tasks: BackgroundTasks = None
):
    if not playlistURL:
        raise HTTPException(status_code=400, detail="playlistURL required")

    background_tasks.add_task(download_playlist, playlistURL, quality)

    return {"status": "queued", "playlist": playlistURL, "quality": quality}
