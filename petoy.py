import os
import requests
import json
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import psycopg2
from psycopg2.extras import RealDictCursor

# Enable logging
logging.basicConfig(level=logging.INFO)

# Environment variables
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not GROQ_API_KEY:
    logging.error("❌ GROQ_API_KEY is not set!")
    exit(1)

if not TELEGRAM_BOT_TOKEN:
    logging.error("❌ TELEGRAM_BOT_TOKEN is not set!")
    exit(1)

if not DATABASE_URL:
    logging.error("❌ DATABASE_URL is not set!")
    exit(1)

logging.info("✅ Environment variables loaded successfully.")

# ============================================
# TRULY PERMANENT DATABASE (Supabase PostgreSQL)
# ============================================
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("✅ Database initialized successfully!")
    except Exception as e:
        logging.error(f"❌ Database init error: {e}")

def save_message(user_id, role, content):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"❌ Save error: {e}")

def get_history(user_id):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT role, content FROM messages WHERE user_id = %s ORDER BY id",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as e:
        logging.error(f"❌ Get history error: {e}")
        return []

# Initialize database
init_db()

# ============================================
# FLASK SERVER
# ============================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🤖 Petoy 2.0 is running on Groq with TRULY PERMANENT memory!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=10000)

# ============================================
# GROQ API with Truly Permanent Memory
# ============================================
def ask_groq_with_memory(user_id, question):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Build messages with FULL history from database
    messages = [
        {"role": "system", "content": "You are Petoy, a helpful AI assistant created by Jay. You remember EVERYTHING the user has told you."}
    ]
    
    # Add ALL conversation history for this user from database
    history = get_history(user_id)
    for msg in history:
        messages.append(msg)
    
    # Add the current question
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
            
            # Save to database
            save_message(user_id, "user", question)
            save_message(user_id, "assistant", reply)
            
            return reply
        else:
            return f"Error: {result}"
    except Exception as e:
        return f"Error: {e}"

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
    await update.message.reply_text("🤖 Hello! I'm Petoy 2.0 with TRULY PERMANENT memory! I'll remember everything you tell me — even if I restart or Render deletes my disk. 🚀")

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info("🚀 Petoy 2.0 starting with Groq and TRULY PERMANENT memory...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("✅ Petoy 2.0 is running on Groq with TRULY PERMANENT MEMORY!")
    app.run_polling()

if __name__ == "__main__":
    main()