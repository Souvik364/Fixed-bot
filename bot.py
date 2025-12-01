# ------------------------------------------------------
# Telegram Bot — Greeting Reply + Admin Connect System
# ------------------------------------------------------

import os
import asyncio
import logging
import re
import time
import random
import threading
from flask import Flask # New: For Keep Alive
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    PicklePersistence
)

from google import genai

# -------------------- LOAD ENV --------------------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "0")

# -------------------- KEEP ALIVE SERVER --------------------
# This small web server runs alongside your bot.
# It listens for "pings" from UptimeRobot to stay awake.
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    # Render assigns a port via the PORT env var, default to 8080
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

# -------------------- SETUP CHECKS --------------------
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    # On Render, we might set env vars in the dashboard, so we warn instead of exit locally
    print("⚠️ Warning: Tokens not found in .env. Ensure they are set in Render Dashboard.")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0

# -------------------- LOGGING --------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
log = logging.getLogger(__name__)

# Initialize Gemini
try:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    log.error(f"Gemini Init Error: {e}")

# -------------------- UTILS --------------------
def detect_language(text: str) -> str:
    if re.search(r'[\u0980-\u09FF]', text):
        return "bengali"
    return "english"

async def type_animation(update: Update, context):
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except:
        pass

async def safe_ask_gemini(prompt: str) -> str:
    try:
        return await ask_gemini(prompt)
    except:
        return "Message sent ✅"

async def ask_gemini(prompt: str) -> str:
    def call():
        try:
            resp = genai_client.models.generate_content(
                model="gemini-2.0-flash", 
                contents=prompt
            )
            if hasattr(resp, "text") and resp.text:
                return resp.text.strip()
            return "Message sent ✅"
        except Exception as e:
            log.error(f"Gemini API Error: {e}")
            return "Message sent ✅"
    return await asyncio.to_thread(call)

def user_spam(update, context) -> bool:
    now = time.time()
    last = context.user_data.get("last_time", 0)
    if now - last < 1.2:
        return True
    context.user_data["last_time"] = now
    return False

persistence = PicklePersistence(filepath="bot_data.pkl")

# -------------------- HANDLERS --------------------
async def smart_welcome(name: str, lang: str) -> str:
    prompt = f"Write a very short friendly welcome message for {name}. Language: {lang}. Max 1 emoji."
    reply = await safe_ask_gemini(prompt)
    return reply.strip()

async def start_cmd(update, context):
    await type_animation(update, context)
    name = update.effective_user.first_name or "Friend"
    welcome = await smart_welcome(name, "english")
    await update.message.reply_text(welcome)

async def available_cmd(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.bot_data["admin_available"] = True
    context.bot_data["admin_status_changed"] = "available"
    await update.message.reply_text("🟢 Admin Available")

async def away_cmd(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.bot_data["admin_available"] = False
    context.bot_data["admin_status_changed"] = "away"
    await update.message.reply_text("🔴 Admin Away")

async def admin_reply_handler(update, context):
    if update.effective_user.id != ADMIN_ID: return
    reply = update.message.reply_to_message
    if not reply: return
    
    # Get original user ID from the map
    user_id = context.bot_data.get("forwarded_map", {}).get(reply.message_id)
    if user_id:
        try:
            await context.bot.send_message(chat_id=user_id, text=update.message.text)
            await update.message.reply_text("Sent ✅")
        except:
            await update.message.reply_text("❌ Failed (User blocked bot?)")

async def photo_handler(update, context):
    if update.effective_user.id == ADMIN_ID: return
    try:
        fwd = await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except: pass
    await update.message.reply_text("Message sent ✅")

async def handle_message(update, context):
    if update.effective_user.id == ADMIN_ID: return
    if user_spam(update, context): return

    text = update.message.text
    if text.lower().startswith(('hi', 'hello', 'hey', 'start')):
        await type_animation(update, context)
        welcome = await smart_welcome(update.effective_user.first_name, detect_language(text))
        return await update.message.reply_text(welcome)

    # Forward to Admin
    try:
        fwd = await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except: pass

    # Status Response
    status_changed = context.bot_data.get("admin_status_changed")
    if status_changed == "available":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🟢 Admin available.")
    
    if not context.bot_data.get("admin_available", False) and not context.user_data.get("busy_shown"):
        context.user_data["busy_shown"] = True
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅")

    await update.message.reply_text("Message sent ✅")

# -------------------- MAIN --------------------
def main():
    # 1. Start the dummy web server
    keep_alive()
    
    # 2. Start the Bot
    print("🚀 Bot is polling...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("available", available_cmd))
    app.add_handler(CommandHandler("away", away_cmd))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, admin_reply_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()
