import asyncio
import os
import subprocess
import time
import urllib.parse
from aiohttp import web
import aiohttp
from pyrogram import Client, filters

# Render environment variables se credentials uthana
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client("movie_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def human_readable_size(size_bytes):
  if size_bytes == 0:
    return "0 B"
  size_name = ("B", "KB", "MB", "GB", "TB")
  i = 0
  while size_bytes >= 1024 and i < len(size_name) - 1:
    size_bytes /= 1024.0
    i += 1
  return f"{size_bytes:.2f} {size_name[i]}"


async def download_file(url, output_file, message):
  start_time = time.time()
  # URL ko properly encode karna taaki special characters/spaces fail na ho
  safe_url = urllib.parse.quote(url, safe=":/?&=%-_.")

  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )
  }

  async with aiohttp.ClientSession() as session:
    async with session.get(safe_url, headers=headers) as response:
      if response.status != 200:
        await message.edit_text(
            f"❌ Link se file download nahi ho payi. Status: {response.status}"
        )
        return False

      total_size = int(response.headers.get("content-length", 0))
      downloaded = 0
      chunk_size = 1024 * 512
      last_update_time = 0

      with open(output_file, "wb") as f:
        async for chunk in response.content.iter_chunked(chunk_size):
          if chunk:
            f.write(chunk)
            downloaded += len(chunk)
            current_time = time.time()

            if current_time - last_update_time > 3:
              last_update_time = current_time
              elapsed_time = current_time - start_time
              speed = downloaded / elapsed_time if elapsed_time > 0 else 0
              percentage = (
                  (downloaded / total_size) * 100 if total_size > 0 else 0
              )

              if speed > 0:
                eta_seconds = (total_size - downloaded) / speed
                eta_str = f"{int(eta_seconds)}s"
              else:
                eta_str = "Calculating..."

              completed_blocks = int(percentage // 10)
              progress_bar = "█" * completed_blocks + "░" * (
                  10 - completed_blocks
              )

              text = (
                  f"📥 **Downloading Movie...**\n\n"
                  f"[{progress_bar}] **{percentage:.1f}%**\n\n"
                  f"🚀 **Speed:** `{human_readable_size(speed)}/s`\n"
                  f"📦 **Size:** `{human_readable_size(downloaded)} /"
                  f" {human_readable_size(total_size)}`\n"
                  f"⏳ **ETA:** `{eta_str}`"
              )
              try:
                await message.edit_text(text)
              except Exception:
                pass
  return True


@app.on_message(filters.command("start"))
async def start_handler(client, message):
  await message.reply_text(
      "👋 Hello!\n\nMujhe movie ki direct download link bhejiye. Main live"
      " speed, size, aur percentage ke sath download karunga aur sirf **Hindi"
      " audio** wali nayi link dunga!"
  )


@app.on_message(filters.text & ~filters.command("start"))
async def link_handler(client, message):
  url = message.text.strip()
  if not url.startswith("http"):
    await message.reply_text("❌ Kripya ek valid download link bhejiye!")
    return

  status_msg = await message.reply_text(
      "📥 **Connecting to server and downloading...**"
  )

  input_file = "input_movie.mkv"
  output_file = "output_hindi.mkv"

  try:
    # 1. Download file
    success = await download_file(url, input_file, status_msg)
    if not success:
      return

    await status_msg.edit_text(
        "⚙️ **Processing with FFmpeg (Keeping only Hindi Audio)...**"
    )

    # 2. FFmpeg se sirf Hindi audio track rakhna aur video quality original rakhna
    cmd = [
        "ffmpeg",
        "-i",
        input_file,
        "-map",
        "0:v",
        "-map",
        "0:a:m:language:hin",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        output_file,
        "-y",
    ]

    process = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    if process.returncode != 0:
      # Agar specific 'hin' tag na mile toh default pehla audio ya fallback try kar sakte hain
      cmd_fallback = [
          "ffmpeg",
          "-i",
          input_file,
          "-map",
          "0:v",
          "-map",
          "0:a:0",
          "-c:v",
          "copy",
          "-c:a",
          "aac",
          output_file,
          "-y",
      ]
      process = subprocess.run(
          cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE
      )
      if process.returncode != 0:
        await status_msg.edit_text(
            "❌ FFmpeg processing error aa gaya!"
        )
        return

    await status_msg.edit_text(
        "✅ Movie process ho gayi hai! (Abhi upload ka feature joda jayega)"
    )

  except Exception as e:
    await status_msg.edit_text(f"❌ Error aa gaya: {str(e)}")

  finally:
    # Cleanup temporary files
    for f in [input_file, output_file]:
      if os.path.exists(f):
        os.remove(f)


# Render ke port scan timeout error ko fix karne ke liye aiohttp web server
async def handle(request):
  return web.Response(text="Bot is running!")


async def start_web_server():
  app_web = web.Application()
  app_web.add_routes([web.get("/", handle)])
  runner = web.AppRunner(app_web)
  await runner.setup()
  site = web.TCPSite(runner, "0.0.0.0", 10000)
  await site.start()


if __name__ == "__main__":
  loop = asyncio.get_event_loop()
  loop.create_task(start_web_server())
  app.run()
