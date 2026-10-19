import urllib.parse  # Is module ko import section mein jodh dein (agar pehle se nahi hai)


async def download_file(url, output_file, message):
  start_time = time.time()

  # URL ko properly encode/parse karne ke liye
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
