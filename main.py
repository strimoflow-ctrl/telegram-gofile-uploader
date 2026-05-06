import os
import asyncio
import aiohttp
import aiofiles
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename, DocumentAttributeVideo
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── ENV VARS ──────────────────────────────────────────────
API_ID        = int(os.environ["API_ID"])
API_HASH      = os.environ["API_HASH"]
SESSION_STRING= os.environ["SESSION_STRING"]
BOT_TOKEN     = os.environ["BOT_TOKEN"]
GOFILE_API_KEY= os.environ["GOFILE_API_KEY"]
GOFILE_FOLDER = os.environ["GOFILE_FOLDER_ID"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])   # your personal TG user ID
GROUP_ID      = int(os.environ.get("GROUP_ID", "-1003879918144"))
TOPIC_ID      = int(os.environ.get("TOPIC_ID", "572"))
DOWNLOAD_DIR  = "/tmp/tg_downloads"
LINKS_FILE    = "links.txt"
PROGRESS_FILE = "progress.txt"           # stores last processed index
# ─────────────────────────────────────────────────────────

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── helpers ───────────────────────────────────────────────

def parse_caption(caption: str):
    """Extract Index, Title, Batch from caption."""
    index = title = batch = None
    if not caption:
        return index, title, batch
    for line in caption.splitlines():
        line = line.strip()
        if line.lower().startswith("index"):
            m = re.search(r'[\d]+', line)
            if m:
                index = m.group()
        elif line.lower().startswith("title"):
            title = line.split(":", 1)[-1].strip()
            # remove @mentions
            title = re.sub(r'@\w+', '', title).strip()
            # remove .mp4 / .pdf suffix
            title = re.sub(r'\.(mp4|pdf|mkv|avi)$', '', title, flags=re.IGNORECASE).strip()
        elif line.lower().startswith("batch"):
            batch = line.split(":", 1)[-1].strip()
    return index, title, batch


def get_filename(msg) -> str:
    for attr in msg.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return f"file_{msg.id}"


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return int(f.read().strip())
    return 0   # start from beginning (index 0 = oldest msg)


def save_progress(pos: int):
    with open(PROGRESS_FILE, "w") as f:
        f.write(str(pos))


async def get_gofile_server(session: aiohttp.ClientSession) -> str:
    async with session.get("https://api.gofile.io/servers") as r:
        data = await r.json()
    servers = data["data"]["servers"]
    return servers[0]["name"]


async def upload_to_gofile(session: aiohttp.ClientSession, filepath: str, filename: str) -> str:
    server = await get_gofile_server(session)
    url = f"https://{server}.gofile.io/contents/uploadfile"
    async with aiofiles.open(filepath, "rb") as f:
        file_bytes = await f.read()
    data = aiohttp.FormData()
    data.add_field("file", file_bytes, filename=filename, content_type="application/octet-stream")
    data.add_field("token", GOFILE_API_KEY)
    data.add_field("folderId", GOFILE_FOLDER)
    async with session.post(url, data=data) as r:
        resp = await r.json()
    if resp.get("status") != "ok":
        raise Exception(f"GoFile error: {resp}")
    return resp["data"]["downloadPage"]


async def send_bot_msg(bot: TelegramClient, text: str):
    try:
        await bot.send_message(OWNER_CHAT_ID, text, parse_mode="md")
    except Exception as e:
        logger.warning(f"Bot msg failed: {e}")


# ── main ──────────────────────────────────────────────────

async def main():
    # two clients: user (download) + bot (notify)
    user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    bot_client  = TelegramClient("bot_session", API_ID, API_HASH)

    await user_client.start()
    await bot_client.start(bot_token=BOT_TOKEN)

    await send_bot_msg(bot_client, "🚀 **Uploader started!** Fetching messages...")

    # collect all media messages from topic
    logger.info("Collecting messages from topic...")
    all_msgs = []
    async for msg in user_client.iter_messages(GROUP_ID, reply_to=TOPIC_ID, limit=None):
        if msg.document:
            all_msgs.append(msg)

    # oldest first
    all_msgs.reverse()
    total = len(all_msgs)
    logger.info(f"Found {total} media messages")
    await send_bot_msg(bot_client, f"📦 Total files found: **{total}**\nStarting upload...")

    start_pos = load_progress()
    done = start_pos
    failed = []

    async with aiohttp.ClientSession() as http:
        for i, msg in enumerate(all_msgs):
            if i < start_pos:
                continue   # resume

            filename = get_filename(msg)
            caption  = msg.message or ""
            idx, title, batch = parse_caption(caption)

            # fallback index
            display_idx = idx if idx else str(i + 1).zfill(3)
            display_title = title if title else filename
            display_batch = batch if batch else "Unknown"

            filepath = os.path.join(DOWNLOAD_DIR, filename)
            try:
                # ── download ──
                logger.info(f"[{i+1}/{total}] Downloading: {filename}")
                await user_client.download_media(msg, file=filepath)

                # ── upload ──
                logger.info(f"[{i+1}/{total}] Uploading to GoFile: {filename}")
                gofile_link = await upload_to_gofile(http, filepath, filename)

                # ── save link ──
                line = f"{display_idx} | {display_title} | {display_batch} | {gofile_link}\n"
                async with aiofiles.open(LINKS_FILE, "a") as lf:
                    await lf.write(line)

                # ── cleanup ──
                os.remove(filepath)
                done += 1
                save_progress(i + 1)

                # notify every 10 files
                if done % 10 == 0 or done == total:
                    remaining = total - done
                    await send_bot_msg(
                        bot_client,
                        f"📊 **Progress Update**\n"
                        f"✅ Done: {done}/{total}\n"
                        f"⏳ Remaining: {remaining}\n"
                        f"📁 Last: `{display_title}`"
                    )

            except Exception as e:
                logger.error(f"Failed [{filename}]: {e}")
                failed.append(filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
                await send_bot_msg(bot_client, f"⚠️ Failed: `{filename}`\nError: {e}")

    # ── final report ──
    summary = (
        f"🎉 **Upload Complete!**\n"
        f"✅ Uploaded: {done}\n"
        f"❌ Failed: {len(failed)}\n"
    )
    if failed:
        summary += "Failed files:\n" + "\n".join(f"• `{f}`" for f in failed[:20])

    await send_bot_msg(bot_client, summary)
    logger.info("All done!")

    await user_client.disconnect()
    await bot_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
