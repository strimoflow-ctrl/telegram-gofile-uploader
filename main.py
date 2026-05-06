import os
import asyncio
import aiohttp
import aiofiles
import logging
import json
import time
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ID         = int(os.environ["API_ID"])
API_HASH       = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
BOT_TOKEN      = os.environ["BOT_TOKEN"]
GOFILE_API_KEY = os.environ["GOFILE_API_KEY"]
GOFILE_FOLDER  = os.environ["GOFILE_FOLDER_ID"]
OWNER_CHAT_ID  = int(os.environ["OWNER_CHAT_ID"])
GROUP_ID       = int(os.environ.get("GROUP_ID", "-1003879918144"))
TOPIC_ID       = int(os.environ.get("TOPIC_ID", "572"))
DOWNLOAD_DIR   = "/tmp/tg_downloads"
PROGRESS_GOFILE_NAME = "_progress.json"
LINKS_GOFILE_NAME    = "_links.txt"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
bot_client  = None
user_client = None

async def send_msg(text, msg_id=None):
    global bot_client
    try:
        if msg_id:
            await bot_client.edit_message(OWNER_CHAT_ID, msg_id, text, parse_mode="md")
            return msg_id
        else:
            msg = await bot_client.send_message(OWNER_CHAT_ID, text, parse_mode="md")
            return msg.id
    except Exception as e:
        logger.warning(f"Bot msg error: {e}")
        try:
            msg = await bot_client.send_message(OWNER_CHAT_ID, text, parse_mode="md")
            return msg.id
        except:
            return None

def progress_bar(done, total, width=12):
    pct = done / total if total else 0
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return bar, round(pct * 100, 1)

def format_size(b):
    if b < 1024**2: return f"{b/1024:.1f}KB"
    elif b < 1024**3: return f"{b/1024**2:.1f}MB"
    else: return f"{b/1024**3:.2f}GB"

def eta_str(elapsed, done_now, remaining_total):
    if done_now == 0: return "calculating..."
    rate = done_now / elapsed
    secs = remaining_total / rate
    h, m = int(secs//3600), int((secs%3600)//60)
    return f"{h}h {m}m" if h > 0 else f"{m}m"

async def get_gofile_server(session):
    async with session.get("https://api.gofile.io/servers") as r:
        data = await r.json()
    return data["data"]["servers"][0]["name"]

async def upload_to_gofile(session, filepath, filename):
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
    return resp["data"]["downloadPage"], resp["data"]["id"]

async def upload_text_to_gofile(session, content, filename):
    server = await get_gofile_server(session)
    url = f"https://{server}.gofile.io/contents/uploadfile"
    data = aiohttp.FormData()
    data.add_field("file", content.encode(), filename=filename, content_type="text/plain")
    data.add_field("token", GOFILE_API_KEY)
    data.add_field("folderId", GOFILE_FOLDER)
    async with session.post(url, data=data) as r:
        resp = await r.json()
    if resp.get("status") != "ok":
        raise Exception(f"GoFile text upload error: {resp}")
    return resp["data"]["id"]

async def delete_gofile_content(session, content_id):
    try:
        url = f"https://api.gofile.io/contents/{content_id}"
        headers = {"Authorization": f"Bearer {GOFILE_API_KEY}"}
        async with session.delete(url, headers=headers) as r:
            pass
    except Exception as e:
        logger.warning(f"GoFile delete warning: {e}")

async def get_folder_contents(session):
    url = f"https://api.gofile.io/contents/{GOFILE_FOLDER}"
    headers = {"Authorization": f"Bearer {GOFILE_API_KEY}"}
    async with session.get(url, headers=headers) as r:
        resp = await r.json()
    if resp.get("status") != "ok":
        return {}
    return resp["data"].get("children", {})

async def load_persistent_state(session):
    progress = {"done_indices": [], "last_index": 0}
    links_content = ""
    progress_file_id = None
    links_file_id = None
    try:
        children = await get_folder_contents(session)
        for cid, child in children.items():
            name = child.get("name", "")
            dl_link = child.get("link", "")
            if name == PROGRESS_GOFILE_NAME and dl_link:
                progress_file_id = cid
                async with session.get(dl_link) as r:
                    text = await r.text()
                progress = json.loads(text)
                logger.info(f"Resumed from index: {progress['last_index']}")
            elif name == LINKS_GOFILE_NAME and dl_link:
                links_file_id = cid
                async with session.get(dl_link) as r:
                    links_content = await r.text()
    except Exception as e:
        logger.warning(f"Could not load state: {e}")
    return progress, links_content, progress_file_id, links_file_id

async def save_state(session, progress, links_content, progress_file_id, links_file_id):
    if progress_file_id:
        await delete_gofile_content(session, progress_file_id)
    if links_file_id:
        await delete_gofile_content(session, links_file_id)
    new_pid = await upload_text_to_gofile(session, json.dumps(progress), PROGRESS_GOFILE_NAME)
    new_lid = await upload_text_to_gofile(session, links_content, LINKS_GOFILE_NAME)
    return new_pid, new_lid

def parse_caption(caption):
    index = title = batch = None
    if not caption: return index, title, batch
    for line in caption.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("index"):
            m = re.search(r'\d+', line)
            if m: index = m.group().zfill(3)
        elif low.startswith("title"):
            title = line.split(":", 1)[-1].strip()
            title = re.sub(r'@\w+', '', title).strip()
            title = re.sub(r'\.(mp4|pdf|mkv|avi)$', '', title, flags=re.IGNORECASE).strip()
        elif low.startswith("batch"):
            batch = line.split(":", 1)[-1].strip()
    return index, title, batch

def get_filename(msg):
    for attr in msg.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    ext = "." + msg.document.mime_type.split("/")[-1] if msg.document.mime_type else ""
    return f"file_{msg.id}{ext}"

async def main():
    global bot_client, user_client
    user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    bot_client  = TelegramClient("bot", API_ID, API_HASH)
    await user_client.start()
    await bot_client.start(bot_token=BOT_TOKEN)

    status_id = await send_msg(
        "🔄 *Initializing Uploader...*\n\n"
        "📡 Connected to Telegram\n"
        "📂 Loading previous progress from GoFile..."
    )

    async with aiohttp.ClientSession() as http:
        progress, links_content, prog_fid, links_fid = await load_persistent_state(http)
        done_indices = set(progress.get("done_indices", []))
        already_done = len(done_indices)

        await send_msg(
            "📋 *Fetching file list from Telegram group...*\n"
            "_(May take 1-2 mins for large groups)_",
            status_id
        )

        all_msgs = []
        async for msg in user_client.iter_messages(GROUP_ID, reply_to=TOPIC_ID, limit=None):
            if msg.document:
                all_msgs.append(msg)
        all_msgs.reverse()

        total = len(all_msgs)
        remaining = total - already_done
        start_time = time.time()
        failed = []
        current_done = already_done

        await send_msg(
            f"🚀 *Upload Started!*\n\n"
            f"{'─'*20}\n"
            f"📦 Total Files:   `{total}`\n"
            f"✅ Already Done:  `{already_done}`\n"
            f"⏳ To Upload:     `{remaining}`\n"
            f"{'─'*20}\n\n"
            f"_Progress saves every 5 files to GoFile_\n"
            f"_Safe to restart anytime!_ ✨",
            status_id
        )

        for i, msg in enumerate(all_msgs):
            if i in done_indices:
                continue

            filename = get_filename(msg)
            idx, title, batch = parse_caption(msg.message or "")
            display_idx   = idx if idx else str(i + 1).zfill(3)
            display_title = title if title else filename
            display_batch = batch if batch else "Unknown"
            ftype = "📄" if filename.endswith(".pdf") else "🎬" if filename.endswith((".mp4",".mkv",".avi")) else "📁"
            filepath = os.path.join(DOWNLOAD_DIR, filename)

            bar, pct = progress_bar(current_done, total)
            elapsed = max(time.time() - start_time, 1)
            done_this_session = current_done - already_done
            eta = eta_str(elapsed, done_this_session, remaining - done_this_session)

            await send_msg(
                f"⬇️ *Downloading...*\n\n"
                f"{ftype} *{display_title}*\n"
                f"📋 #{display_idx} | 📚 {display_batch}\n\n"
                f"`{bar}` {pct}%\n"
                f"📊 `{current_done}` / `{total}` files\n"
                f"⏱ ETA: `{eta}`",
                status_id
            )

            try:
                await user_client.download_media(msg, file=filepath)
                fsize = format_size(os.path.getsize(filepath))

                await send_msg(
                    f"⬆️ *Uploading to GoFile...*\n\n"
                    f"{ftype} *{display_title}*\n"
                    f"📦 Size: `{fsize}` | #{display_idx}\n\n"
                    f"`{bar}` {pct}%\n"
                    f"📊 `{current_done}` / `{total}` files\n"
                    f"⏱ ETA: `{eta}`",
                    status_id
                )

                gofile_link, _ = await upload_to_gofile(http, filepath, filename)
                os.remove(filepath)

                links_content += f"{display_idx} | {display_title} | {display_batch} | {gofile_link}\n"
                current_done += 1
                done_indices.add(i)
                progress["last_index"] = i + 1
                progress["done_indices"] = list(done_indices)

                if current_done % 5 == 0 or current_done == total:
                    prog_fid, links_fid = await save_state(http, progress, links_content, prog_fid, links_fid)
                    saved_tick = "💾 _Progress saved!_"
                else:
                    saved_tick = ""

                bar2, pct2 = progress_bar(current_done, total)
                done_now = current_done - already_done
                eta2 = eta_str(max(time.time()-start_time,1), done_now, remaining - done_now)

                await send_msg(
                    f"✅ *Done!*\n\n"
                    f"{ftype} *{display_title}*\n"
                    f"🔗 [Open on GoFile]({gofile_link})\n\n"
                    f"`{bar2}` {pct2}%\n"
                    f"📊 `{current_done}` / `{total}` files\n"
                    f"⏱ ETA: `{eta2}`\n"
                    f"{saved_tick}",
                    status_id
                )

            except Exception as e:
                logger.error(f"Failed [{filename}]: {e}")
                failed.append(display_title)
                if os.path.exists(filepath):
                    os.remove(filepath)
                await send_msg(
                    f"⚠️ *Skipped (Error)*\n\n"
                    f"`{display_title}`\n"
                    f"_{str(e)[:150]}_\n\n"
                    f"_Continuing with next file..._",
                    status_id
                )
                await asyncio.sleep(3)

        # Final save
        await save_state(http, progress, links_content, prog_fid, links_fid)

        failed_text = ""
        if failed:
            failed_text = "\n\n❌ *Failed:*\n" + "\n".join(f"• `{f}`" for f in failed[:15])

        await send_msg(
            f"🎉 *ALL DONE!*\n\n"
            f"{'─'*20}\n"
            f"✅ Uploaded:  `{current_done}`\n"
            f"❌ Failed:    `{len(failed)}`\n"
            f"📦 Total:     `{total}`\n"
            f"{'─'*20}\n\n"
            f"📄 `_links.txt` saved in your GoFile folder!\n"
            f"🔄 _Restart safe - progress stored in GoFile_"
            + failed_text,
            status_id
        )

    await user_client.disconnect()
    await bot_client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
