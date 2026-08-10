import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, DATABASE_URL]):
    logging.error("❌ Missing environment variables!")
    exit(1)

# ============================================
# DATABASE
# ============================================
def init_db():
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
    logging.info("✅ Database ready")

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
        logging.info(f"💾 Saved: {content[:30]}...")
    except Exception as e:
        logging.error(f"❌ Save error: {e}")

def get_history(user_id):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT role, content FROM messages WHERE user_id = %s ORDER BY id LIMIT 15",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as e:
        logging.error(f"❌ History error: {e}")
        return []

init_db()

# ============================================
# FLASK
# ============================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Petoy 2.0 is running!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

# ============================================
# GROQ AI
# ============================================
def ask_groq(user_id, question):
    import requests
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    messages = [
        {"role": "system", "content": "You are Petoy, an AI assistant created by Jay. You remember everything. Respond in Filipino if asked."}
    ]
    
    history = get_history(user_id)
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        reply = data["choices"][0]["message"]["content"]
        save_message(user_id, "user", question)
        save_message(user_id, "assistant", reply)
        return reply
    except Exception as e:
        logging.error(f"❌ Groq error: {e}")
        return "Error. Please try again."

# ============================================
# TELEGRAM
# ============================================
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Hello! I'm Petoy 2.0.\n\n"
        "💬 I remember everything you tell me.\n"
        "🇵🇭 I can speak Filipino too!\n"
        "🧠 Just chat with me normally."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        text = update.message.text
        logging.info(f"📩 {user_id}: {text}")
        
        reply = ask_groq(user_id, text)
        await update.message.reply_text(reply)
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        await update.message.reply_text("Error. Please try again.")

# ============================================
# MAIN
# ============================================
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info("🚀 Petoy 2.0 starting...")
    
    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("✅ Petoy 2.0 is running!")
    bot.run_polling()

if __name__ == "__main__":
    main()