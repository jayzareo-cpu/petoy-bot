import os
import requests
import json
import logging
import threading
import re
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
    logging.error("❌ DATABASE_URL is not set! Memory will not work!")
    exit(1)

logging.info("✅ Environment variables loaded successfully.")

# ============================================
# PERMANENT DATABASE (Supabase PostgreSQL)
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
        logging.info("✅ Supabase database initialized!")
    except Exception as e:
        logging.error(f"❌ Database init error: {e}")

def save_message(user_id, role, content):
    try:
        logging.info(f"💾 Saving: user={user_id}, role={role}, content={content[:30]}...")
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
        conn.commit()
        conn.close()
        logging.info("✅ Message saved successfully!")
    except Exception as e:
        logging.error(f"❌ Save error: {e}")

def get_history(user_id):
    try:
        logging.info(f"📚 Fetching history for user: {user_id}")
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT role, content FROM messages WHERE user_id = %s ORDER BY id",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        logging.info(f"📚 Retrieved {len(rows)} messages for user {user_id}")
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as e:
        logging.error(f"❌ Get history error: {e}")
        return []

init_db()

# ============================================
# FLASK SERVER
# ============================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🤖 Petoy 2.0 is running with Supabase memory!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=10000)

# ============================================
# IMAGE GENERATOR (Pollinations AI)
# ============================================
def generate_image(prompt):
    enhanced_prompt = f"{prompt}, high quality, detailed, 4k, photorealistic"
    url = f"https://image.pollinations.ai/prompt/{enhanced_prompt.replace(' ', '%20')}"
    
    try:
        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            return response.content
        else:
            logging.error(f"Pollinations error: {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"Image generation error: {e}")
        return None

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
# NATURAL LANGUAGE IMAGE TRIGGERS
# ============================================
def extract_image_prompt(text):
    triggers = [
        r'make me (?:an? )?image of (.+)',
        r'make me (?:an? )?picture of (.+)',
        r'generate (?:an? )?image of (.+)',
        r'create (?:an? )?image of (.+)',
        r'draw (?:an? )?image of (.+)',
        r'show me (?:an? )?image of (.+)',
        r'i want (?:an? )?image of (.+)',
        r'image of (.+)',
        r'picture of (.+)',
    ]
    
    image_keywords = ['image', 'picture', 'photo', 'draw', 'generate', 'create', 'make me']
    if not any(keyword in text.lower() for keyword in image_keywords):
        return None
    
    for pattern in triggers:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    for word in image_keywords:
        if word in text.lower():
            cleaned = re.sub(word, '', text, flags=re.IGNORECASE).strip()
            if cleaned:
                return cleaned
    
    return None

# ============================================
# TELEGRAM HANDLERS
# ============================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        logging.info(f"👤 User ID: {user_id}")
        
        user_message = update.message.text
        logging.info(f"📩 Received: {user_message}")
        
        image_prompt = extract_image_prompt(user_message)
        
        if image_prompt:
            logging.info(f"🖼️ Image requested: {image_prompt}")
            await update.message.reply_text("🎨 Generating your image... (this may take a few seconds)")
            
            image_data = generate_image(image_prompt)
            
            if image_data:
                await update.message.reply_photo(
                    photo=image_data,
                    caption=f"🖼️ Here's your image: {image_prompt}"
                )
            else:
                await update.message.reply_text("❌ Sorry, I couldn't generate that image. Please try a different prompt.")
            return
        
        reply = ask_groq_with_memory(user_id, user_message)
        await update.message.reply_text(reply)
        
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        await update.message.reply_text(f"Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    await update.message.reply_text(
        "🤖 Hello! I'm Petoy 2.0 with image generation!\n\n"
        "💬 You can chat with me normally, or say:\n"
        "• 'make me an image of a cat'\n"
        "• 'generate a picture of a sunset'\n"
        "• 'draw a dragon'\n"
        "• 'show me a photo of a robot'\n\n"
        "🧠 I remember everything you tell me — even after restarts!"
    )

# ============================================
# MAIN
# ============================================
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logging.info("🚀 Petoy 2.0 starting with image generation...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("✅ Petoy 2.0 is running with Groq AI + Image Generation!")
    app.run_polling()

if __name__ == "__main__":
    main()