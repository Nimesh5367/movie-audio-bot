import asyncio
import os
import time
import json
import urllib.parse
from aiohttp import web
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

# -------------------------------------------------------------
# 1. RENDER & FFMPEG BINARY RESOLVER
# Render par missing FFmpeg binary error ko automatically fix karta hai
# -------------------------------------------------------------
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    print("✅ static-ffmpeg binaries loaded successfully into PATH!")
except ImportError:
    print("ℹ️ static-ffmpeg not imported, using system ffmpeg.")

# Render environment variables se credentials uthana
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "10000"))

app = Client("movie_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# -------------------------------------------------------------
# 2. HELPER UTILITIES
# -------------------------------------------------------------
def human_readable_size(size_bytes: float) -> str:
    """Format bytes into readable string."""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while size_bytes >= 1024 and i < len(size_name) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_name[i]}"


def build_progress_bar(percentage: float, length: int = 10) -> str:
    """Generate visual progress bar: [██████░░░░]"""
    filled = int(percentage // (100 / length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def upload_progress_callback(current: int, total: int, status_msg: Message, start_time: float):
    """
    Throttled Telegram upload progress reporter (Har 3.5s me update).
    Telegram FLOOD_WAIT error se bachata hai.
    """
    if total <= 0:
        return

    now = time.time()
    last_update = getattr(status_msg, "_last_upload_time", 0)
    if now - last_update < 3.5 and current < total:
        return

    status_msg._last_upload_time = now
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
                    await message.edit_text(f"❌ Link se download fail ho gaya. Status: {response.status}")
                    return False

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 1024 * 1024  # 1MB buffer
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
# 4. ASYNC NON-BLOCKING FFPROBE & FFMPEG (DEADLOCK FIX)
# -------------------------------------------------------------
async def run_async_command(cmd: list) -> tuple:
    """
    CRITICAL FIX: communicate() se 64KB pipe buffer deadlock kabhi nahi hota,
    aur asyncio loop kabhi freeze nahi hota!
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore')


async def probe_audio_streams(file_path: str):
    """Container se audio streams ko scan karta hai."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-select_streams", "a",
        "-of", "json",
        file_path
    ]
    try:
        code, stdout, _ = await run_async_command(cmd)
        if code == 0:
            data = json.loads(stdout)
            return data.get("streams", [])
    except Exception as e:
        print(f"ffprobe error: {e}")
    return []


async def get_video_metadata(file_path: str):
    """Duration, width, height for smooth Telegram video player."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=width,height",
        "-select_streams", "v:0",
        "-of", "json",
        file_path
    ]
    try:
        code, stdout, _ = await run_async_command(cmd)
        if code == 0:
            data = json.loads(stdout)
            duration = int(float(data.get("format", {}).get("duration", 0)))
            streams = data.get("streams", [])
            width = streams[0].get("width", 1280) if streams else 1280
            height = streams[0].get("height", 720) if streams else 720
            return duration, width, height
    except Exception:
        pass
    return 0, 1280, 720


async def generate_thumbnail(video_path: str, thumb_path: str, timestamp_sec: int = 10) -> bool:
    """1-frame thumbnail create karta hai Telegram preview ke liye."""
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
        code, _, _ = await run_async_command(cmd)
        return code == 0 and os.path.exists(thumb_path)
    except Exception:
        return False


# -------------------------------------------------------------
# 5. BOT MESSAGE HANDLERS
# -------------------------------------------------------------
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 **Namaste!**\n\n"
        "Mujhe direct movie link bhejiye. Main live progress ke sath download karke "
        "sirf **Hindi Audio** extract karke seedha Telegram video deliver karunga!"
    )


@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def link_handler(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        await message.reply_text("❌ Kripya valid http/https download link bhejiye!")
        return

    status_msg = await message.reply_text("📥 **Connecting to server and downloading...**")

    task_id = int(time.time())
    input_file = f"input_{task_id}.mkv"
    output_file = f"output_hindi_{task_id}.mkv"
    thumb_file = f"thumb_{task_id}.jpg"

    try:
        # Step 1: Download
        success = await download_file(url, input_file, status_msg)
        if not success or not os.path.exists(input_file):
            return

        await status_msg.edit_text("🔍 **Audio streams scan ho rahe hain (FFprobe)...**")

        # Step 2: Probe audio tracks
        audio_streams = await probe_audio_streams(input_file)
        hindi_stream_index = None
        track_label = "Hindi Audio"

        if audio_streams:
            for s in audio_streams:
                tags = s.get("tags", {})
                lang = tags.get("language", "").lower()
                title = tags.get("title", "").lower()
                codec = s.get("codec_name", "").lower()

                # Hindi match criteria (tag ya title me 'hin', 'hi', ya 'hindi')
                if lang in ["hin", "hi", "hindi"] or "hindi" in title:
                    hindi_stream_index = s.get("index")
                    track_label = f"Hindi ({codec.upper()})"
                    break

        await status_msg.edit_text(
            f"⚙️ **Processing with FFmpeg (Keeping only Hindi Audio)...**\n"
            f"• Stream Target: `{track_label}`\n"
            f"• Mode: Lossless Stream Copy (-c:v copy -c:a copy)\n"
            f"• Speed: ~5 Seconds!"
        )

        # Step 3: FAST LOSSLESS REMUXING
        # -c:a copy audio ko re-encode nahi karta, isliye 5 second me complete hota hai!
        if hindi_stream_index is not None:
            cmd = [
                "ffmpeg",
                "-i", input_file,
                "-map", "0:v:0",
                "-map", f"0:{hindi_stream_index}",
                "-c:v", "copy",
                "-c:a", "copy",
                "-sn",
                output_file,
                "-y"
            ]
        else:
            # Fallback 1: Language metadata ya first audio
            cmd = [
                "ffmpeg",
                "-i", input_file,
                "-map", "0:v:0",
                "-map", "0:a:m:language:hin?",
                "-map", "0:a:0?",
                "-c:v", "copy",
                "-c:a", "copy",
                "-sn",
                output_file,
                "-y"
            ]

        code, _, stderr = await run_async_command(cmd)

        # Fallback 2: Agar kisi container me copy mana kare toh AAC me encode karein
        if code != 0 or not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            cmd_aac = [
                "ffmpeg",
                "-i", input_file,
                "-map", "0:v:0",
                "-map", "0:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-sn",
                output_file,
                "-y"
            ]
            code, _, stderr = await run_async_command(cmd_aac)
            if code != 0:
                await status_msg.edit_text(f"❌ FFmpeg error: {stderr[-250:]}")
                return

        # Step 4: Size Check (Telegram bot limit 2.0 GB)
        output_size = os.path.getsize(output_file)
        if output_size > 2 * 1024 * 1024 * 1024:
            await status_msg.edit_text(
                f"⚠️ File size ({human_readable_size(output_size)}) Telegram bot 2.0 GB limit se bada hai."
            )
            return

        await status_msg.edit_text("📤 **Telegram par upload shuru ho raha hai...**")

        # Metadata aur Thumbnail
        duration, width, height = await get_video_metadata(output_file)
        thumb_ok = await generate_thumbnail(output_file, thumb_file, timestamp_sec=min(15, max(1, duration // 10)))
        thumb_path = thumb_file if thumb_ok and os.path.exists(thumb_file) else None

        upload_start = time.time()
        caption = (
            f"🎬 **Processed Movie (Hindi Audio)**\n\n"
            f"🔊 **Audio:** {track_label}\n"
            f"📦 **Size:** `{human_readable_size(output_size)}`\n"
            f"⏱️ **Duration:** `{duration // 60}m {duration % 60}s`\n"
            f"⚡ **Processed by:** Movie Audio Bot"
        )

        # Step 5: Send Video directly to Telegram with progress bar!
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
            progress_args=(status_msg, upload_start)
        )

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Error occurred: {str(e)}")

    finally:
        # Render disk cleanup
        for f in [input_file, output_file, thumb_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass


# -------------------------------------------------------------
# 6. RENDER HEALTH CHECK SERVER
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
    print(f"🚀 Render health check server running on port {PORT}")
    return runner


# -------------------------------------------------------------
# 7. CONCURRENT ASYNC MAIN
# -------------------------------------------------------------
async def main():
    web_runner = await start_web_server()
    print("🤖 Starting Pyrogram Telegram Client...")
    await app.start()
    me = await app.get_me()
    print(f"✅ Bot started successfully as @{me.username} (ID: {me.id})")
    
    import pyrogram
    await pyrogram.idle()
    
    print("🛑 Shutting down...")
    await app.stop()
    await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
