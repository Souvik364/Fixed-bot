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
from dotenv import load_dotenv
from flask import Flask  # REQUIRED for Render Web Service

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

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("⚠️ CRITICAL: Tokens not found. Check Environment Variables.")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0


# -------------------- KEEP ALIVE SERVER --------------------
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

try:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    log.error(f"Gemini Init Error: {e}")
    genai_client = None


# -------------------- UTILS --------------------
def detect_language(text: str) -> str:
    if text and re.search(r'[\u0980-\u09FF]', text):
        return "bengali"
    return "english"

async def type_animation(update: Update, context, text="💬 Bot is typing…", delay=1.5):
    msg = None
    try:
        msg = await update.message.reply_text(text)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except:
        pass
    await asyncio.sleep(delay)
    if msg:
        try:
            await msg.delete()
        except:
            pass

async def send_temp_confirmation(update: Update, text="Message sent ✅", delay=5):
    try:
        msg = await update.message.reply_text(text)
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception:
        pass


# -------------------- AI & FALLBACK --------------------
FALLBACK_RESPONSES = ["Message sent ✅"]

async def safe_ask_gemini(prompt: str) -> str:
    try:
        return await ask_gemini(prompt)
    except:
        return random.choice(FALLBACK_RESPONSES)

async def ask_gemini(prompt: str) -> str:
    if not genai_client: return "Message sent ✅"
    def call():
        try:
            resp = genai_client.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            if hasattr(resp, "text") and resp.text:
                return resp.text.strip()
            return "Message sent ✅"
        except:
            return "Message sent ✅"
    return await asyncio.to_thread(call)


# -------------------- SPAM CONTROL --------------------
def user_spam(update, context) -> bool:
    now = time.time()
    last = context.user_data.get("last_time", 0)
    if now - last < 1.2: return True
    context.user_data["last_time"] = now
    return False


# -------------------- PERSISTENCE --------------------
persistence = PicklePersistence(filepath="bot_data.pkl")


# -------------------- HANDLERS --------------------
async def smart_welcome(name: str, lang: str) -> str:
    prompt = f"Make a short friendly welcome message for {name}. Language: {lang}. Max 1 emoji."
    reply = await safe_ask_gemini(prompt)
    return reply.strip()

async def start_cmd(update, context):
    name = update.effective_user.first_name or "Friend"
    welcome = await smart_welcome(name, "english")
    await update.message.reply_text(welcome)

async def available_cmd(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.bot_data["admin_available"] = True
    context.bot_data["admin_status_changed"] = "available"
    await update.message.reply_text("🟢 Admin is now available.")

async def away_cmd(update, context):
    if update.effective_user.id != ADMIN_ID: return
    context.bot_data["admin_available"] = False
    context.bot_data["admin_status_changed"] = "away"
    await update.message.reply_text("🔴 Admin is now away.")


# -------------------- ADMIN REPLY (UPDATED) --------------------
async def admin_reply_handler(update, context):
    # 1. Verify it's the Admin
    if update.effective_user.id != ADMIN_ID:
        return

    # 2. Verify they are replying to something
    reply_msg = update.message.reply_to_message
    if not reply_msg:
        return

    # 3. Find the original user ID
    forwarded_id = reply_msg.message_id
    user_chat = context.bot_data.get("forwarded_map", {}).get(forwarded_id)

    if not user_chat:
        return await update.message.reply_text("❌ User not found (Too old?).")

    try:
        # 4. Check if Admin sent a PHOTO
        if update.message.photo:
            photo_id = update.message.photo[-1].file_id # Get largest size
            caption = update.message.caption
            await context.bot.send_photo(chat_id=user_chat, photo=photo_id, caption=caption)
        
        # 5. Check if Admin sent TEXT
        elif update.message.text:
            await context.bot.send_message(chat_id=user_chat, text=update.message.text)
        
        # 6. Confirm success
        asyncio.create_task(send_temp_confirmation(update))

    except Exception as e:
        log.error(f"Reply Error: {e}")
        await update.message.reply_text("❌ Failed to send.")


# -------------------- USER MESSAGE HANDLERS --------------------
async def photo_handler(update: Update, context):
    if update.effective_user.id == ADMIN_ID: return
    try:
        fwd = await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except: pass
    
    await type_animation(update, context)
    asyncio.create_task(send_temp_confirmation(update))

async def handle_message(update, context):
    if update.effective_user.id == ADMIN_ID: return
    if user_spam(update, context): return

    text = update.message.text
    if not text: return # Ignore non-text updates here

    # Greetings check
    greetings = ["hi", "hello", "hey", "hlo", "hola", "namaste", "salam", "assalamualaikum"]
    if text.lower().split()[0] in greetings:
        lang = detect_language(text)
        name = update.effective_user.first_name or "Friend"
        greeting = await smart_welcome(name, lang)
        return await update.message.reply_text(greeting)

    # Forward to Admin
    try:
        fwd = await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except: pass

    await type_animation(update, context)

    # Status Response
    status = context.bot_data.get("admin_status_changed")
    if status == "available":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🟢 Admin available.")
    if status == "away":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    if context.bot_data.get("admin_available", False):
        return asyncio.create_task(send_temp_confirmation(update))

    if not context.user_data.get("busy_shown", False):
        context.user_data["busy_shown"] = True
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    return asyncio.create_task(send_temp_confirmation(update))


# -------------------- MAIN --------------------
def main():
    keep_alive()
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("available", available_cmd))
    app.add_handler(CommandHandler("away", away_cmd))
    
    # --- UPDATED ADMIN FILTER: Allows TEXT OR PHOTO replies ---
    app.add_handler(MessageHandler(filters.REPLY & (filters.TEXT | filters.PHOTO), admin_reply_handler))
    
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
