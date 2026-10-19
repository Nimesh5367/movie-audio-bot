import asyncio
import math
import os
import time
import subprocess
from pyrogram import Client, filters
import aiohttp

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client("movie_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# Progress bar generate karne ka function
def human_readable_size(size_bytes):
  if size_bytes == 0:
    return "0 B"
  size_name = ("B", "KB", "MB", "GB", "TB")
  i = int(math.floor(math.log(size_bytes, 1024)))
  p = math.pow(1024, i)
  s = round(size_bytes / p, 2)
  return f"{s} {size_name[i]}"


async def download_file(url, output_file, message):
  start_time = time.time()
  async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
      if response.status != 200:
        await message.edit_text("❌ Link se file download nahi ho payi. Error!")
        return False

      total_size = int(response.headers.get("content-length", 0))
      downloaded = 0
      chunk_size = 1024 * 512  # 512KB chunks
      last_update_time = 0

      with open(output_file, "wb") as f:
        async for chunk in response.content.iter_chunked(chunk_size):
          if chunk:
            f.write(chunk)
            downloaded += len(chunk)
            current_time = time.time()

            # Har 3 seconds mein message update hoga taaki Telegram flood limit na aaye
            if current_time - last_update_time > 3:
              last_update_time = current_time
              elapsed_time = current_time - start_time

              if elapsed_time > 0:
                speed = downloaded / elapsed_time  # Bytes per second
              else:
                speed = 0

              percentage = (
                  (downloaded / total_size) * 100 if total_size > 0 else 0
              )

              # ETA calculation
              if speed > 0:
                eta_seconds = (total_size - downloaded) / speed
                eta_str = f"{int(eta_seconds)}s"
              else:
                eta_str = "Calculating..."

              # Progress bar visual
              completed_blocks = int(percentage // 10)
              progress_bar = "█" * completed_blocks + "░" * (
                  10 - completed_blocks
              )

              text = (
                  f"📥 **Downloading Movie...**\n\n"
                  f"[{progress_bar}] **{percentage:.1f}%**\n\n"
                  f"🚀 **Speed:** `{human_readable_size(speed)}/s`\n"
                  f"📦 **Size:** `{human_readable_size(downloaded)} / {human_readable_size(total_size)}`\n"
                  f"⏳ **ETA:** `{eta_str}`"
              )
              try:
                await message.edit_text(text)
              except Exception:
                pass
  return True


@app.on_message(filters.command("start"))
async def start_msg(client, message):
  await message.reply_text(
      "👋 Hello!\n\nMujhe movie ki direct download link bhejiye. "
      "Main live speed, size, aur percentage ke sath download karunga aur sirf **Hindi audio** wali nayi link dunga!"
  )


@app.on_message(filters.text & ~filters.command("start"))
async def process_movie(client, message):
  url = message.text.strip()
  if not url.startswith("http"):
    await message.reply_text("❌ Kripya ek valid direct download link bhejiye.")
    return

  status_msg = await message.reply_text(
      "🔄 Link analyze ho rahi hai..."
  )

  input_file = "input_movie.mkv"
  output_file = "output_hindi.mkv"

  try:
    # 1. Live Progress ke sath download
    success = await download_file(url, input_file, status_msg)
    if not success:
      return

    await status_msg.edit_text(
        "⚙️ **FFmpeg Processing:** Hindi audio select ho raha hai (Video Quality 100% Original)..."
    )

    # 2. FFmpeg Command (Sirf Hindi audio rakhna, baaki remove karna)
    cmd = [
        "ffmpeg",
        "-i",
        input_file,
        "-map",
        "0:v",
        "-map",
        "0:a:m:language:hin?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-y",
        output_file,
    ]

    process = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )

    if process.returncode != 0:
      # Fallback agar hin tag na mile toh pehla audio lo
      cmd = [
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
          "copy",
          "-y",
          output_file,
      ]
      subprocess.run(cmd)

    await status_msg.edit_text(
        "📤 **Uploading:** Nayi movie file hosting par upload ho rahi hai..."
    )

    # 3. Gofile par upload
    async with aiohttp.ClientSession() as session:
      with open(output_file, "rb") as f:
        data = aiohttp.FormData()
        data.add_field(
            "file", f, filename="movie_hindi.mkv", content_type="video/mkv"
        )
        async with session.post(
            "https://store1.gofile.io/contents/uploadfile", data=data
        ) as resp:
          upload_res = await resp.json()

    if upload_res.get("status") == "ok":
      download_link = upload_res["data"]["downloadPage"]
      file_size = human_readable_size(os.path.getsize(output_file))
      await status_msg.edit_text(
          f"✅ **Process Complete!**\n\n"
          f"🎬 **Final File Size:** `{file_size}`\n"
          f"🔗 **Hindi Audio Movie Link:**\n{download_link}"
      )
    else:
      await status_msg.edit_text(
          "❌ Upload karne mein kuch error aa gaya."
      )

  except Exception as e:
    await status_msg.edit_text(f"❌ Error aa gaya: {str(e)}")

  finally:
    if os.path.exists(input_file):
      os.remove(input_file)
    if os.path.exists(output_file):
      os.remove(output_file)


app.run()
