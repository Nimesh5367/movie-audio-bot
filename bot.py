import asyncio
import os
import subprocess
import time
import json
import urllib.parse
from aiohttp import web
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

# -------------------------------------------------------------
# 1. RENDER & FFMPEG SETUP
# Render par FFmpeg missing error fix karne ke liye static-ffmpeg
# -------------------------------------------------------------
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    print("✅ static-ffmpeg binaries loaded successfully!")
except ImportError:
    print("ℹ️ static-ffmpeg package not found, relying on system ffmpeg.")

# Render environment variables se credentials uthana
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "10000"))

app = Client("movie_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# -------------------------------------------------------------
# 2. HELPER FUNCTIONS
# -------------------------------------------------------------
def human_readable_size(size_bytes: float) -> str:
    """Bytes ko KB, MB, GB me convert karta hai."""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while size_bytes >= 1024 and i < len(size_name) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_name[i]}"


def build_progress_bar(percentage: float, length: int = 10) -> str:
    """Visual progress bar banata hai: [██████░░░░]"""
    filled = int(percentage // (100 / length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def upload_progress_callback(current: int, total: int, status_msg: Message, start_time: float):
    """
    Telegram upload progress update karta hai.
    Telegram FLOOD_WAIT se bachne ke liye har 3.5 seconds me update hota hai.
    """
    if total <= 0:
        return

    now = time.time()
    last_update = getattr(status_msg, "_last_upload_update", 0)
    if now - last_update < 3.5 and current < total:
        return

    status_msg._last_upload_update = now
    elapsed = max(0.1, now - start_time)
    speed = current / elapsed
    percentage = min(100.0, (current / total) * 100)
    remaining_bytes = max(0, total - current)
    eta_sec = int(remaining_bytes / speed) if speed > 0 else 0
    eta_str = f"{eta_sec}s" if eta_sec > 0 else "Almost done"

    bar = build_progress_bar(percentage, length=10)
    text = (
        f"📤 **Uploading to Telegram...**\n\n"
        f"[{bar}] **{percentage:.1f}%**\n\n"
        f"🚀 **Speed:** `{human_readable_size(speed)}/s`\n"
        f"📦 **Size:** `{human_readable_size(current)} / {human_readable_size(total)}`\n"
        f"⏳ **ETA:** `{eta_str}`"
    )
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass


# -------------------------------------------------------------
# 3. DIRECT DOWNLOAD ENGINE
# -------------------------------------------------------------
async def download_file(url: str, output_file: str, message: Message) -> bool:
    start_time = time.time()
    safe_url = urllib.parse.quote(url, safe=":/?&=%-_.")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(safe_url, headers=headers, timeout=aiohttp.ClientTimeout(total=3600)) as response:
                if response.status != 200:
                    await message.edit_text(f"❌ Link se file download nahi ho payi. HTTP Status: {response.status}")
                    return False

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 1024 * 1024  # 1MB buffer tezi se download ke liye
                last_update_time = 0

                with open(output_file, "wb") as f:
                    async for chunk in response.content.iter_chunked(chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        current_time = time.time()

                        if current_time - last_update_time > 3.5 or downloaded == total_size:
                            last_update_time = current_time
                            elapsed = max(0.1, current_time - start_time)
                            speed = downloaded / elapsed
                            percentage = (downloaded / total_size) * 100 if total_size > 0 else 0
                            eta_sec = int((total_size - downloaded) / speed) if speed > 0 and total_size > 0 else 0
                            eta_str = f"{eta_sec}s" if eta_sec > 0 else "Calculating..."
                            bar = build_progress_bar(percentage, length=10)

                            text = (
                                f"📥 **Downloading Movie...**\n\n"
                                f"[{bar}] **{percentage:.1f}%**\n\n"
                                f"🚀 **Speed:** `{human_readable_size(speed)}/s`\n"
                                f"📦 **Size:** `{human_readable_size(downloaded)} / {human_readable_size(total_size)}`\n"
                                f"⏳ **ETA:** `{eta_str}`"
                            )
                            try:
                                await message.edit_text(text)
                            except Exception:
                                pass
                return True
        except Exception as e:
            await message.edit_text(f"❌ Download error: {str(e)}")
            return False


# -------------------------------------------------------------
# 4. SMART FFPROBE & METADATA DETECTOR
# -------------------------------------------------------------
def probe_audio_streams(file_path: str):
    """File me se saare audio tracks aur unki language check karta hai."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-select_streams", "a",
        "-of", "json",
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("streams", [])
    except Exception as e:
        print(f"ffprobe error: {e}")
    return []


def get_video_metadata(file_path: str):
    """Telegram inline player ke liye duration, width aur height nikalta hai."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=width,height",
        "-select_streams", "v:0",
        "-of", "json",
        file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            duration = int(float(data.get("format", {}).get("duration", 0)))
            streams = data.get("streams", [])
            width = streams[0].get("width", 1280) if streams else 1280
            height = streams[0].get("height", 720) if streams else 720
            return duration, width, height
    except Exception:
        pass
    return 0, 1280, 720


def generate_thumbnail(video_path: str, thumb_path: str, timestamp_sec: int = 10) -> bool:
    """Telegram video preview ke liye 1 second ka snapshot banata hai."""
    cmd = [
        "ffmpeg",
        "-ss", str(timestamp_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        thumb_path,
        "-y"
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return res.returncode == 0 and os.path.exists(thumb_path)
    except Exception:
        return False


# -------------------------------------------------------------
# 5. TELEGRAM BOT HANDLERS
# -------------------------------------------------------------
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 **Namaste!**\n\n"
        "Mujhe kisi bhi movie ya video ki direct download link bhejiye.\n\n"
        "⚡ **Features:**\n"
        "1. Live download speed aur percentage progress bar\n"
        "2. Sirf **Hindi Audio** track extract karega (video quality 100% original rahegi)\n"
        "3. Telegram par direct playable video stream ke sath send karega!"
    )


@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def link_handler(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        await message.reply_text("❌ Kripya ek valid direct download link bhejiye!")
        return

    status_msg = await message.reply_text("📥 **Connecting to server and downloading...**")

    # Unique filenames taaki multiple users par file clash na ho
    task_id = int(time.time())
    input_file = f"input_{task_id}.mkv"
    output_file = f"output_hindi_{task_id}.mp4"
    thumb_file = f"thumb_{task_id}.jpg"

    try:
        # Step 1: Video download karein
        success = await download_file(url, input_file, status_msg)
        if not success or not os.path.exists(input_file):
            return

        await status_msg.edit_text("🔍 **Audio tracks scan ho rahe hain (FFprobe)...**")

        # Step 2: Audio tracks me se Hindi track dhoondein
        audio_streams = probe_audio_streams(input_file)
        hindi_stream_index = None
        track_label = "Hindi Audio"

        if audio_streams:
            for s in audio_streams:
                tags = s.get("tags", {})
                lang = tags.get("language", "").lower()
                title = tags.get("title", "").lower()
                if lang in ["hin", "hi", "hindi"] or "hindi" in title:
                    hindi_stream_index = s.get("index")
                    track_label = f"Hindi ({s.get('codec_name', 'aac').upper()})"
                    break

        await status_msg.edit_text(
            f"⚙️ **Processing with FFmpeg...**\n"
            f"Detected: `{track_label}`\n"
            f"• Video: Lossless Copy (-c:v copy)\n"
            f"• Audio: Hindi AAC (-c:a aac)\n"
            f"• FastStart: Enabled"
        )

        # Step 3: FFmpeg command run karein (Video copy + Hindi audio AAC)
        if hindi_stream_index is not None:
            cmd = [
                "ffmpeg",
                "-i", input_file,
                "-map", "0:v:0",
                "-map", f"0:{hindi_stream_index}",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                output_file,
                "-y"
            ]
        else:
            # Fallback agar metadata tag na mile
            cmd = [
                "ffmpeg",
                "-i", input_file,
                "-map", "0:v:0",
                "-map", "0:a:m:language:hin?",
                "-map", "0:a:0?",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                output_file,
                "-y"
            ]

        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Step 3.1: Agar pehla command fail ho toh default pehla audio track try karein
        if process.returncode != 0 or not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            fallback_cmd = [
                "ffmpeg",
                "-i", input_file,
                "-map", "0:v:0",
                "-map", "0:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                output_file,
                "-y"
            ]
            process = subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if process.returncode != 0:
                await status_msg.edit_text("❌ FFmpeg processing me error aa gaya!")
                return

        # Step 4: Size check (Telegram bot limit 2.0 GB)
        output_size = os.path.getsize(output_file)
        if output_size > 2 * 1024 * 1024 * 1024:
            await status_msg.edit_text(
                f"⚠️ Processed file size `{human_readable_size(output_size)}` Telegram ki 2GB limit se bada hai!"
            )
            return

        await status_msg.edit_text("📤 **Telegram par upload shuru ho raha hai...**")

        # Video metadata aur thumbnail nikalna
        duration, width, height = get_video_metadata(output_file)
        thumb_success = generate_thumbnail(output_file, thumb_file, timestamp_sec=min(15, max(1, duration // 10)))
        thumb_path = thumb_file if thumb_success and os.path.exists(thumb_file) else None

        upload_start_time = time.time()
        caption = (
            f"🎬 **Processed Movie (Hindi Audio)**\n\n"
            f"🔊 **Audio:** {track_label}\n"
            f"📦 **Size:** `{human_readable_size(output_size)}`\n"
            f"⏱️ **Duration:** `{duration // 60}m {duration % 60}s`\n"
            f"⚡ **Processed by:** Movie Audio Bot"
        )

        # Step 5: Telegram par Video send karein with live upload progress!
        await client.send_video(
            chat_id=message.chat.id,
            video=output_file,
            caption=caption,
            duration=duration,
            width=width,
            height=height,
            thumb=thumb_path,
            supports_streaming=True,
            progress=upload_progress_callback,
            progress_args=(status_msg, upload_start_time)
        )

        # Upload complete hone par progress message delete karein
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Error aa gaya: {str(e)}")

    finally:
        # Step 6: Render ke disk space ko full hone se bachane ke liye files cleanup
        for f in [input_file, output_file, thumb_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass


# -------------------------------------------------------------
# 6. RENDER UPTIME WEB SERVER (PORT SCAN FIX)
# -------------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Bot is running! CineAudio Engine is Live.")

async def start_web_server():
    app_web = web.Application()
    app_web.add_routes([web.get("/", handle_ping), web.get("/health", handle_ping)])
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🚀 Render health server listening on port {PORT}")
    return runner


# -------------------------------------------------------------
# 7. MAIN ASYNC RUNNER
# -------------------------------------------------------------
async def main():
    web_runner = await start_web_server()
    print("🤖 Starting Pyrogram Telegram Client...")
    await app.start()
    me = await app.get_me()
    print(f"✅ Bot is Live as @{me.username} (ID: {me.id})")
    
    # Bot ko continuously run rakhne ke liye
    import pyrogram
    await pyrogram.idle()
    
    # Graceful shutdown
    print("🛑 Stopping Bot and Web Server...")
    await app.stop()
    await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
