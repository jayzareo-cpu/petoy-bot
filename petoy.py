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
# DATABASE (Supabase)
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
                zodiac TEXT,
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
        logging.info(f"💾 Message saved for {user_id}")
    except Exception as e:
        logging.error(f"❌ Save message error: {e}")

def get_history(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT role, content FROM messages WHERE user_id = %s ORDER BY id LIMIT 20",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as e:
        logging.error(f"❌ History error: {e}")
        return []

def save_user_info(user_id, name=None, birthday=None, zodiac=None, facts=None):
    try:
        logging.info(f"🔥 SAVE_USER_INFO CALLED for {user_id}: name={name}, birthday={birthday}, zodiac={zodiac}")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, name, birthday, zodiac, facts)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name,
                birthday = EXCLUDED.birthday,
                zodiac = EXCLUDED.zodiac,
                facts = EXCLUDED.facts
            """,
            (user_id, name, birthday, zodiac, facts)
        )
        conn.commit()
        conn.close()
        logging.info(f"✅ Saved user: name={name}, birthday={birthday}, zodiac={zodiac}")
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

def extract_all_info(text):
    """Extract name, birthday, zodiac, and any other facts from the text."""
    info = {}

    name_match = re.search(r'my name is (\w+)', text, re.IGNORECASE)
    if name_match:
        info['name'] = name_match.group(1)

    birthday_match = re.search(r'my birthday is (\w+ \d+)', text, re.IGNORECASE)
    if not birthday_match:
        birthday_match = re.search(r'birthday is (\w+ \d+)', text, re.IGNORECASE)
    if birthday_match:
        info['birthday'] = birthday_match.group(1)

    zodiac_match = re.search(r'i\'?m a (\w+)', text, re.IGNORECASE)
    if zodiac_match:
        possible_zodiac = zodiac_match.group(1).lower()
        zodiacs = ['pisces', 'aries', 'taurus', 'gemini', 'cancer', 'leo', 'virgo',
                   'libra', 'scorpio', 'sagittarius', 'capricorn', 'aquarius']
        if possible_zodiac in zodiacs:
            info['zodiac'] = possible_zodiac.capitalize()

    return info

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
# GROQ AI
# ============================================
def ask_groq(user_id, question):
    user_info = get_user_info(user_id)

    context = ""
    if user_info:
        if user_info.get("name"):
            context += f"The user's name is {user_info['name']}. "
        if user_info.get("birthday"):
            context += f"Their birthday is {user_info['birthday']}. "
        if user_info.get("zodiac"):
            context += f"Their zodiac sign is {user_info['zodiac']}. "
        if user_info.get("facts"):
            context += f"Additional facts: {user_info['facts']}. "
    else:
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
        return reply
    except Exception as e:
        logging.error(f"❌ Groq error: {e}")
        return "Error. Please try again."

# ============================================
# TELEGRAM HANDLERS
# ============================================
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Hello! I'm Petoy 2.0.\n\n"
        "🧠 I remember EVERYTHING you tell me.\n"
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

        # --- Image request ---
        image_prompt = extract_image_prompt(text)
        if image_prompt:
            await update.message.reply_text("🎨 Generating...")
            image_data = generate_image(image_prompt)
            if image_data:
                await update.message.reply_photo(photo=image_data, caption=f"🖼️ {image_prompt}")
            else:
                await update.message.reply_text("❌ Failed to generate image.")
            return

        # --- Auto-save personal info ---
        extracted = extract_all_info(text)
        current = get_user_info(user_id) or {}
        if extracted:
            save_user_info(
                user_id,
                name=extracted.get('name') or current.get('name'),
                birthday=extracted.get('birthday') or current.get('birthday'),
                zodiac=extracted.get('zodiac') or current.get('zodiac'),
                facts=text  # Save the whole message as a fact
            )
            logging.info(f"✅ Auto-saved info for {user_id}")

        # --- Normal chat ---
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
    bot.add_handler(CommandHandler("image", image))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.info("✅ Petoy 2.0 is running!")
    bot.run_polling()

if __name__ == "__main__":
    main()