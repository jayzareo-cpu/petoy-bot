import os
import logging
import threading
import re
import requests
import psycopg2
import psycopg2.extras
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

logging.basicConfig(level=logging.INFO)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, DATABASE_URL]):
    logging.error("❌ Missing environment variables!")
    exit(1)

# ============================================
# DATABASE (Everything is saved here)
# ============================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_db_connection()
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                birthday TEXT,
                facts TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("✅ Database ready")
    except Exception as e:
        logging.error(f"❌ DB init error: {e}")

def save_message(user_id, role, content):
    try:
        conn = get_db_connection()
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
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT role, content FROM messages WHERE user_id = %s ORDER BY id LIMIT 30",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as e:
        logging.error(f"❌ History error: {e}")
        return []

def save_user_info(user_id, name=None, birthday=None, facts=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, name, birthday, facts)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name,
                birthday = EXCLUDED.birthday,
                facts = EXCLUDED.facts
            """,
            (user_id, name, birthday, facts)
        )
        conn.commit()
        conn.close()
        logging.info(f"✅ User info saved: name={name}, birthday={birthday}")
    except Exception as e:
        logging.error(f"❌ Save user error: {e}")

def get_user_info(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        logging.error(f"❌ Get user error: {e}")
        return None

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
# IMAGE GENERATOR
# ============================================
def generate_image(prompt):
    enhanced_prompt = f"{prompt}, high quality, detailed, 4k"
    url = f"https://image.pollinations.ai/prompt/{enhanced_prompt.replace(' ', '%20')}"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            return response.content
        return None
    except:
        return None

def extract_image_prompt(text):
    triggers = [
        r'make me (?:an? )?image of (.+)',
        r'generate (?:an? )?image of (.+)',
        r'create (?:an? )?image of (.+)',
        r'draw (?:an? )?image of (.+)',
        r'show me (?:an? )?image of (.+)',
        r'image of (.+)',
        r'picture of (.+)',
    ]
    for pattern in triggers:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None

# ============================================
# GROQ AI (Full Memory)
# ============================================
def ask_groq(user_id, question):
    user_info = get_user_info(user_id)
    
    # Build context from everything we know about the user
    context = ""
    if user_info:
        if user_info.get("name"):
            context += f"The user's name is {user_info['name']}. "
        if user_info.get("birthday"):
            context += f"Their birthday is {user_info['birthday']}. "
        if user_info.get("facts"):
            context += f"Extra info: {user_info['facts']}. "
    
    if not context:
        context = "The user hasn't shared any personal info yet."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    messages = [
        {"role": "system", "content": f"You are Petoy, an AI assistant. You remember EVERYTHING about the user. {context} Always respond in English."}
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
        
        # Extract and save any new info from the user's message
        name_match = re.search(r'my name is (\w+)', question, re.IGNORECASE)
        birthday_match = re.search(r'my birthday is (\w+)', question, re.IGNORECASE)
        if name_match or birthday_match or len(question) > 30:
            current_info = get_user_info(user_id) or {}
            save_user_info(
                user_id,
                name=name_match.group(1) if name_match else current_info.get("name"),
                birthday=birthday_match.group(1) if birthday_match else current_info.get("birthday"),
                facts=current_info.get("facts") or ""
            )
        
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
        "🧠 I remember EVERYTHING you tell me:\n"
        "• Your name\n"
        "• Your birthday\n"
        "• Your secrets\n"
        "• Every conversation we've ever had\n\n"
        "🖼️ Say: 'make me an image of a cat'\n"
        "🗣️ I only speak English.\n\n"
        "Tell me something about yourself — I'll never forget."
    )

async def image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = ' '.join(context.args)
    if not prompt:
        await update.message.reply_text("❌ Please provide a prompt! Example: /image a cat in space")
        return
    await update.message.reply_text("🎨 Generating...")
    image_data = generate_image(prompt)
    if image_data:
        await update.message.reply_photo(photo=image_data, caption=f"🖼️ {prompt}")
    else:
        await update.message.reply_text("❌ Failed to generate image.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        text = update.message.text
        logging.info(f"📩 {user_id}: {text}")
        
        # Detect image
        image_prompt = extract_image_prompt(text)
        if image_prompt:
            await update.message.reply_text("🎨 Generating...")
            image_data = generate_image(image_prompt)
            if image_data:
                await update.message.reply_photo(photo=image_data, caption=f"🖼️ {image_prompt}")
            else:
                await update.message.reply_text("❌ Failed to generate image.")
            return
        
        # Normal chat (with full memory)
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
    logging.info("🚀 Petoy 2.0 starting with FULL MEMORY...")
    
    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("image", image))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("✅ Petoy 2.0 is running!")
    bot.run_polling()

if __name__ == "__main__":
    main()