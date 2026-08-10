import os
import requests
import json
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

# Enable logging
logging.basicConfig(level=logging.INFO)

# Environment variables
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not GROQ_API_KEY:
    logging.error("❌ GROQ_API_KEY is not set!")
    exit(1)

if not TELEGRAM_BOT_TOKEN:
    logging.error("❌ TELEGRAM_BOT_TOKEN is not set!")
    exit(1)

logging.info("✅ Environment variables loaded successfully.")

# ============================================
# FLASK SERVER (Keeps Render happy)
# ============================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🤖 Petoy 2.0 is running on Groq!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=10000)

# ============================================
# GROQ API (Llama 3.1 8B Instant)
# ============================================
def ask_groq(question):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3.1-8b-instant",  # Fastest model
        "messages": [
            {"role": "system", "content": "You are Petoy, a helpful AI assistant created by Jay."},
            {"role": "user", "content": question}
        ],
        "temperature": 0.7,
        "max_tokens": 500
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        else:
            return f"Error: {result}"
    except Exception as e:
        return f"Error: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_message = update.message.text
        logging.info(f"📩 Received: {user_message}")
        reply = ask_groq(user_message)
        await update.message.reply_text(reply)
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        await update.message.reply_text(f"Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Hello! I'm Petoy 2.0, now powered by Groq! 🚀")

def main():
    # Start Flask server in a background thread
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info("🚀 Petoy 2.0 starting with Groq...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("✅ Petoy 2.0 is running on Groq (Llama 3.1 8B Instant)!")
    app.run_polling()

if __name__ == "__main__":
    main()