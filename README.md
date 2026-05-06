# Telegram → GoFile Uploader

Automatically downloads media from a Telegram topic and uploads to GoFile.io
<!-- trigger -->
## Railway Environment Variables

Set these in Railway → Project → Variables:

| Variable | Value |
|---|---|
| `API_ID` | From https://my.telegram.org |
| `API_HASH` | From https://my.telegram.org |
| `SESSION_STRING` | Your Telethon session string |
| `BOT_TOKEN` | From @BotFather |
| `GOFILE_API_KEY` | From gofile.io/myProfile |
| `GOFILE_FOLDER_ID` | GoFile folder ID where files go |
| `OWNER_CHAT_ID` | Your Telegram user ID (get from @userinfobot) |
| `GROUP_ID` | `-1003879918144` (already set) |
| `TOPIC_ID` | `572` (already set) |

## link.txt format (output)
```
001 | Fluid And Elasticity L-06 | Achiever Online | https://gofile.io/d/xxxxx
002 | Some Other Title | Achiever Online | https://gofile.io/d/yyyyy
```

## Features
- ✅ Downloads one file at a time (saves disk space)
- ✅ Uploads to your GoFile folder
- ✅ Deletes local file after upload
- ✅ Resume support (if interrupted, starts from where it left off)
- ✅ Telegram bot progress updates every 10 files
- ✅ Final summary with failed files list
- ✅ Processes oldest → newest (index 1 first)

## Deploy Steps
1. Push this repo to GitHub
2. Connect GitHub repo to Railway
3. Add all environment variables
4. Deploy!
