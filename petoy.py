import os
import requests
import json
import logging
import threading
import sqlite3
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
# PERMANENT DATABASE (SQLite)
# ============================================
def init_db():
    conn = sqlite3.connect('conversations.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = sqlite3.connect('conversations.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect('conversations.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in rows]

# Initialize database
init_db()

# ============================================
# FLASK SERVER (Keeps Render happy)
# ============================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🤖 Petoy 2.0 is running on Groq with PERMANENT memory!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=10000)

# ============================================
# GROQ API with Permanent Memory
# ============================================
def ask_groq_with_memory(user_id, question):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    messages = [
        {"role": "system", "content": "You are Petoy, a helpful AI assistant created by Jay. You remember EVERYTHING the user has told you."}
    ]
    
    history = get_history(user_id)
    for msg in history:
        messages.append(msg)
    
    messages.append({"role": "user", "content": question})
    
    data = {
        "model": "llama-3.1-8b-instant",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        if "choices" in result:
            reply = result["choices"][0]["message"]["content"]
            save_message(user_id, "user", question)
            save_message(user_id, "assistant", reply)
            return reply
        else:
            return f"Error: {result}"
    except Exception as e:
        return f"Error: {e}"

# ============================================
# IMAGE GENERATOR (Pollinations AI)
# ============================================
async def image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = ' '.join(context.args)
    if not prompt:
        await update.message.reply_text("❌ Please provide a prompt! Example: /image a cat in space")
        return
    
    image_url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}"
    await update.message.reply_photo(image_url, caption=f"🖼️ Here's your image: {prompt}")

# ============================================
# TELEGRAM HANDLERS
# ============================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        user_message = update.message.text
        logging.info(f"📩 Received from {user_id}: {user_message}")
        
        reply = ask_groq_with_memory(user_id, user_message)
        await update.message.reply_text(reply)
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        await update.message.reply_text(f"Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    await update.message.reply_text("🤖 Hello! I'm Petoy 2.0 with PERMANENT memory! I'll remember everything you tell me, even if I restart. 🚀")

# ============================================
# MAIN
# ============================================
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info("🚀 Petoy 2.0 starting with Groq and PERMANENT memory...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("image", image))  # 🖼️ Image generator
    
    # Message handler (for all other text messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("✅ Petoy 2.0 is running on Groq with PERMANENT MEMORY and IMAGE GENERATION!")
    app.run_polling()

if __name__ == "__main__":
    main()