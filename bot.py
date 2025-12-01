# ------------------------------------------------------
# Telegram Bot — Greeting Reply + Admin Connect System
# Features:
# 1. 24/7 Hosting (Flask Keep-Alive)
# 2. Smart Greeting (Gemini)
# 3. Admin Forwarding & Replying
# 4. Auto-delete confirmation messages
# 5. Visual "Typing..." status
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

# Warning logic for logs (Helpful for debugging on Render)
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("⚠️ CRITICAL: Tokens not found. Check Environment Variables in Render Dashboard.")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0


# -------------------- KEEP ALIVE SERVER --------------------
# Render requires a web server binding to a port to keep the app alive.
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot is alive and running!"

def run_flask():
    # Render assigns a random port to the PORT env var. We must use it.
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


# -------------------- LOGGING --------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
log = logging.getLogger(__name__)

# Initialize Gemini safely
try:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    log.error(f"Gemini Init Error: {e}")
    genai_client = None


# -------------------- UTILS --------------------
def detect_language(text: str) -> str:
    if re.search(r'[\u0980-\u09FF]', text):
        return "bengali"
    return "english"

# -------------------- ANIMATION & AUTO-DELETE --------------------

async def type_animation(update: Update, context, text="💬 Bot is typing…", delay=1.5):
    """Shows typing status, sends text, waits, then deletes text."""
    msg = None
    try:
        # 1. Send visible text
        msg = await update.message.reply_text(text)
        # 2. Send Telegram 'typing' status action
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except:
        pass
    
    await asyncio.sleep(delay)
    
    # 3. Delete the text
    if msg:
        try:
            await msg.delete()
        except:
            pass


async def send_temp_confirmation(update: Update, text="Message sent ✅", delay=5):
    """Sends a confirmation and auto-deletes it."""
    try:
        msg = await update.message.reply_text(text)
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception:
        pass


# -------------------- FALLBACK & AI --------------------
FALLBACK_RESPONSES = ["Message sent ✅"]

async def safe_ask_gemini(prompt: str) -> str:
    try:
        return await ask_gemini(prompt)
    except:
        return random.choice(FALLBACK_RESPONSES)

async def ask_gemini(prompt: str) -> str:
    if not genai_client:
        return "Message sent ✅"

    def call():
        try:
            resp = genai_client.models.generate_content(
                model="gemini-2.0-flash", 
                contents=prompt
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
    if now - last < 1.2:
        return True
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
    lang = "english"
    welcome = await smart_welcome(name, lang)
    await update.message.reply_text(welcome)

async def available_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Not allowed.")
    context.bot_data["admin_available"] = True
    context.bot_data["admin_status_changed"] = "available"
    await update.message.reply_text("🟢 Admin is now available.")

async def away_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Not allowed.")
    context.bot_data["admin_available"] = False
    context.bot_data["admin_status_changed"] = "away"
    await update.message.reply_text("🔴 Admin is now away.")

async def admin_reply_handler(update, context):
    if update.effective_user.id != ADMIN_ID: return
    reply_msg = update.message.reply_to_message
    if not reply_msg: return

    forwarded_id = reply_msg.message_id
    user_chat = context.bot_data.get("forwarded_map", {}).get(forwarded_id)

    if not user_chat:
        return await update.message.reply_text("❌ User not found.")

    try:
        await context.bot.send_message(chat_id=user_chat, text=update.message.text)
        asyncio.create_task(send_temp_confirmation(update))
    except:
        await update.message.reply_text("❌ Failed to send.")

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

    text = update.message.text[:500]
    lang = detect_language(text)
    name = update.effective_user.first_name or "Friend"

    greetings = ["hi", "hello", "hey", "hlo", "hola", "namaste", "salam", "assalamualaikum"]
    if text.lower().split()[0] in greetings:
        greeting = await smart_welcome(name, lang)
        return await update.message.reply_text(greeting)

    try:
        fwd = await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except: pass

    await type_animation(update, context)

    # Status Logic
    status = context.bot_data.get("admin_status_changed")
    if status == "available":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🟢 Admin available.")
    if status == "away":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    # First time vs Available check
    if context.bot_data.get("admin_available", False):
        return asyncio.create_task(send_temp_confirmation(update))

    if not context.user_data.get("busy_shown", False):
        context.user_data["busy_shown"] = True
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    return asyncio.create_task(send_temp_confirmation(update))


# -------------------- MAIN --------------------
def main():
    # 1. Start Web Server (Non-blocking)
    keep_alive()
    
    # 2. Start Bot (Blocking)
    print("🚀 Bot Running...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("available", available_cmd))
    app.add_handler(CommandHandler("away", away_cmd))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, admin_reply_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()    except Exception:
        pass


# -------------------- FALLBACK --------------------
FALLBACK_RESPONSES = ["Message sent ✅"]


# -------------------- SAFE GEMINI CALL --------------------
async def safe_ask_gemini(prompt: str) -> str:
    try:
        return await ask_gemini(prompt)
    except:
        return random.choice(FALLBACK_RESPONSES)


# -------------------- GEMINI MAIN CALL --------------------
async def ask_gemini(prompt: str) -> str:
    if not genai_client:
        return "Message sent ✅"

    def call():
        try:
            resp = genai_client.models.generate_content(
                model="gemini-2.0-flash", 
                contents=prompt
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
    if now - last < 1.2:
        return True
    context.user_data["last_time"] = now
    return False


# -------------------- PERSISTENCE --------------------
persistence = PicklePersistence(filepath="bot_data.pkl")


# ------------------------------------------------------
# SMART GREETING WELCOME
# ------------------------------------------------------
async def smart_welcome(name: str, lang: str) -> str:
    prompt = f"""
Make a short friendly welcome message.
Include user's name: {name}
Language: {lang}
Very short (1–2 lines).
Simple Bengali + English mix.
No emojis at start.
Max one emoji at end.
"""
    reply = await safe_ask_gemini(prompt)
    return reply.strip()


# ------------------------------------------------------
# /start COMMAND
# ------------------------------------------------------
async def start_cmd(update, context):
    name = update.effective_user.first_name or "Friend"
    lang = "english"
    welcome = await smart_welcome(name, lang)
    await update.message.reply_text(welcome)


# ------------------------------------------------------
# ADMIN AVAILABLE / AWAY COMMANDS
# ------------------------------------------------------
async def available_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Not allowed.")

    context.bot_data["admin_available"] = True
    context.bot_data["admin_status_changed"] = "available"
    await update.message.reply_text("🟢 Admin is now available.")


async def away_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Not allowed.")

    context.bot_data["admin_available"] = False
    context.bot_data["admin_status_changed"] = "away"
    await update.message.reply_text("🔴 Admin is now away.")


# ------------------------------------------------------
# ADMIN REPLY HANDLER
# ------------------------------------------------------
async def admin_reply_handler(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    reply_msg = update.message.reply_to_message
    if not reply_msg:
        return

    forwarded_id = reply_msg.message_id
    user_chat = context.bot_data.get("forwarded_map", {}).get(forwarded_id)

    if not user_chat:
        return await update.message.reply_text("❌ User not found.")

    try:
        await context.bot.send_message(chat_id=user_chat, text=update.message.text)
        # Admin gets a temporary confirmation too (keeps chat clean)
        asyncio.create_task(send_temp_confirmation(update))
    except Exception:
        await update.message.reply_text("❌ Failed to send.")


# ------------------------------------------------------
# PHOTO HANDLER
# ------------------------------------------------------
async def photo_handler(update: Update, context):
    if update.effective_user.id == ADMIN_ID:
        return

    try:
        fwd = await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except Exception as e:
        log.error("Photo forward error: %s", e)

    # 1. Show typing animation (text appears then deletes)
    await type_animation(update, context)
    
    # 2. Show confirmation (appears then deletes)
    asyncio.create_task(send_temp_confirmation(update))


# ------------------------------------------------------
# MAIN MESSAGE HANDLER
# ------------------------------------------------------
async def handle_message(update, context):
    if update.effective_user.id == ADMIN_ID:
        return

    if user_spam(update, context):
        return

    text = update.message.text[:500]
    lang = detect_language(text)
    name = update.effective_user.first_name or "Friend"

    # ------------------ GREETING REPLY ------------------
    greetings = ["hi", "hello", "hey", "hlo", "hola", "namaste", "salam", "assalamualaikum"]

    if text.lower() in greetings:
        # Greetings stay permanent (friendly)
        greeting = await smart_welcome(name, lang)
        return await update.message.reply_text(greeting)

    # ------------------ NORMAL LOGIC ---------------------
    try:
        fwd = await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except:
        pass

    # 1. VISIBLE Typing Animation (Text appears -> Waits -> Deletes)
    await type_animation(update, context)

    # Admin status notifications
    admin_status = context.bot_data.get("admin_status_changed")

    if admin_status == "available":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🟢 Admin available.")

    if admin_status == "away":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    # First time user Logic
    first_time = not context.user_data.get("busy_shown", False)
    context.user_data["busy_shown"] = True
    admin_available = context.bot_data.get("admin_available", False)

    if admin_available:
        # Admin is online: Send temporary confirmation
        return asyncio.create_task(send_temp_confirmation(update))

    if first_time:
        # Admin is busy (First time): Send PERMANENT explanation (so they see the 48hr rule)
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    # Admin is busy (Recurring): Send temporary confirmation
    return asyncio.create_task(send_temp_confirmation(update))


# ------------------------------------------------------
# START BOT
# ------------------------------------------------------
def main():
    # Start Keep Alive for 24/7 Hosting
    keep_alive()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("available", available_cmd))
    app.add_handler(CommandHandler("away", away_cmd))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, admin_reply_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Bot Running...")
    app.run_polling()


if __name__ == "__main__":
    main()                return resp.text.strip()
            return "Message sent ✅"
        except:
            return "Message sent ✅"

    return await asyncio.to_thread(call)


# -------------------- SPAM CONTROL --------------------
def user_spam(update, context) -> bool:
    now = time.time()
    last = context.user_data.get("last_time", 0)
    if now - last < 1.2:
        return True
    context.user_data["last_time"] = now
    return False


# -------------------- PERSISTENCE --------------------
persistence = PicklePersistence(filepath="bot_data.pkl")


# ------------------------------------------------------
# SMART GREETING WELCOME
# ------------------------------------------------------
async def smart_welcome(name: str, lang: str) -> str:
    prompt = f"""
Make a short friendly welcome message.
Include user's name: {name}
Language: {lang}
Very short (1–2 lines).
Simple Bengali + English mix.
No emojis at start.
Max one emoji at end.
"""
    reply = await safe_ask_gemini(prompt)
    return reply.strip()


# ------------------------------------------------------
# /start COMMAND
# ------------------------------------------------------
async def start_cmd(update, context):
    name = update.effective_user.first_name or "Friend"
    lang = "english"
    welcome = await smart_welcome(name, lang)
    await update.message.reply_text(welcome)


# ------------------------------------------------------
# ADMIN AVAILABLE / AWAY COMMANDS
# ------------------------------------------------------
async def available_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Not allowed.")

    context.bot_data["admin_available"] = True
    context.bot_data["admin_status_changed"] = "available"
    await update.message.reply_text("🟢 Admin is now available.")


async def away_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Not allowed.")

    context.bot_data["admin_available"] = False
    context.bot_data["admin_status_changed"] = "away"
    await update.message.reply_text("🔴 Admin is now away.")


# ------------------------------------------------------
# ADMIN REPLY HANDLER
# ------------------------------------------------------
async def admin_reply_handler(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    reply_msg = update.message.reply_to_message
    if not reply_msg:
        return

    forwarded_id = reply_msg.message_id
    user_chat = context.bot_data.get("forwarded_map", {}).get(forwarded_id)

    if not user_chat:
        return await update.message.reply_text("❌ User not found.")

    try:
        await context.bot.send_message(chat_id=user_chat, text=update.message.text)
        await update.message.reply_text("Message sent ✅")
    except Exception:
        await update.message.reply_text("❌ Failed to send.")


# ------------------------------------------------------
# PHOTO HANDLER
# ------------------------------------------------------
async def photo_handler(update: Update, context):
    # Don't forward admin photos
    if update.effective_user.id == ADMIN_ID:
        return

    try:
        fwd = await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except Exception as e:
        log.error("Photo forward error: %s", e)

    await update.message.reply_text("Message sent ✅")


# ------------------------------------------------------
# MAIN MESSAGE HANDLER
# ------------------------------------------------------
async def handle_message(update, context):
    # Ignore admin messages
    if update.effective_user.id == ADMIN_ID:
        return

    if user_spam(update, context):
        return

    text = update.message.text[:500]
    lang = detect_language(text)
    name = update.effective_user.first_name or "Friend"

    # ------------------ GREETING REPLY ------------------
    greetings = ["hi", "hello", "hey", "hlo", "hola", "namaste", "salam", "assalamualaikum"]

    if text.lower() in greetings:
        greeting = await smart_welcome(name, lang)
        return await update.message.reply_text(greeting)

    # ------------------ NORMAL LOGIC ---------------------
    try:
        fwd = await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        context.bot_data.setdefault("forwarded_map", {})[fwd.message_id] = update.effective_chat.id
    except:
        pass

    await type_animation(update, context)

    # Admin status notifications
    admin_status = context.bot_data.get("admin_status_changed")

    if admin_status == "available":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🟢 Admin available.")

    if admin_status == "away":
        context.bot_data["admin_status_changed"] = None
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    # First time user
    first_time = not context.user_data.get("busy_shown", False)
    context.user_data["busy_shown"] = True

    admin_available = context.bot_data.get("admin_available", False)

    if admin_available:
        return await update.message.reply_text("Message sent ✅")

    if first_time:
        return await update.message.reply_text("🔴 Admin busy.\nMessage sent ✅\n⏳ Reply within 48 hours.")

    return await update.message.reply_text("Message sent ✅")


# ------------------------------------------------------
# START BOT
# ------------------------------------------------------
def main():
    # START KEEP ALIVE
    keep_alive()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("available", available_cmd))
    app.add_handler(CommandHandler("away", away_cmd))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, admin_reply_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Bot Running...")
    app.run_polling()


if __name__ == "__main__":
    main()
