import os
import logging
import threading
import re
import requests
import psycopg2
import psycopg2.extras
import random
import json
import base64
from datetime import datetime, timedelta
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
# DATABASE
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                task TEXT NOT NULL,
                done BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                remind_at TIMESTAMP NOT NULL,
                done BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("✅ Database ready")
    except Exception as e:
        logging.error(f"❌ DB init error: {e}")

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
# DATABASE FUNCTIONS
# ============================================
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

# ============================================
# SEARCH ENGINE (DuckDuckGo + Wikipedia + IP)
# ============================================
def search_web(query):
    """Search the web using DuckDuckGo (free, no API key)"""
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        results = []
        if data.get("AbstractText"):
            results.append(data["AbstractText"])
        if data.get("RelatedTopics"):
            for topic in data["RelatedTopics"][:3]:
                if topic.get("Text"):
                    results.append(topic["Text"])
        
        if not results:
            return f"🔍 No results found for '{query}'"
        
        return "\n\n".join(results[:3])
    except Exception as e:
        logging.error(f"❌ Search error: {e}")
        return f"❌ Search failed: {e}"

def search_wikipedia(query):
    """Search Wikipedia (free)"""
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("extract"):
            return data["extract"]
        return f"❌ No Wikipedia page found for '{query}'"
    except Exception as e:
        logging.error(f"❌ Wikipedia error: {e}")
        return f"❌ Wikipedia search failed: {e}"

def get_ip_location():
    """Get location from IP (free)"""
    try:
        response = requests.get("http://ip-api.com/json/", timeout=5)
        data = response.json()
        if data.get("status") == "success":
            return f"📍 Location: {data.get('city', 'Unknown')}, {data.get('regionName', 'Unknown')}, {data.get('country', 'Unknown')}\n📡 ISP: {data.get('isp', 'Unknown')}\n🌐 IP: {data.get('query', 'Unknown')}"
        return "❌ Could not get location"
    except Exception as e:
        logging.error(f"❌ IP location error: {e}")
        return "❌ Could not get location"

def search_memory(user_id, query):
    """Search Petoy's memory for info about the user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT role, content FROM messages WHERE user_id = %s AND content ILIKE %s ORDER BY id DESC LIMIT 5",
            (user_id, f"%{query}%")
        )
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return f"🔍 No memory found for '{query}'"
        
        results = []
        for row in rows:
            results.append(f"💬 {row['role']}: {row['content'][:100]}...")
        return "\n\n".join(results)
    except Exception as e:
        logging.error(f"❌ Memory search error: {e}")
        return f"❌ Memory search failed: {e}"

def extract_all_info(text):
    info = {}
    name_match = re.search(r'my name is (\w+)', text, re.IGNORECASE)
    if name_match:
        info['name'] = name_match.group(1)
    birthday_match = re.search(r'my birthday is (\w+ \d+)', text, re.IGNORECASE)
    if birthday_match:
        info['birthday'] = birthday_match.group(1)
    zodiac_match = re.search(r'i\'?m a (\w+)', text, re.IGNORECASE)
    if zodiac_match:
        zodiacs = ['pisces', 'aries', 'taurus', 'gemini', 'cancer', 'leo', 'virgo',
                   'libra', 'scorpio', 'sagittarius', 'capricorn', 'aquarius']
        if zodiac_match.group(1).lower() in zodiacs:
            info['zodiac'] = zodiac_match.group(1).capitalize()
    return info

def add_todo(user_id, task):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO todos (user_id, task) VALUES (%s, %s) RETURNING id",
            (user_id, task)
        )
        todo_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return todo_id
    except Exception as e:
        logging.error(f"❌ Add todo error: {e}")
        return None

def get_todos(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT id, task, done FROM todos WHERE user_id = %s AND done = FALSE ORDER BY created_at",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logging.error(f"❌ Get todos error: {e}")
        return []

def add_reminder(user_id, message, remind_at):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (user_id, message, remind_at) VALUES (%s, %s, %s) RETURNING id",
            (user_id, message, remind_at)
        )
        reminder_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return reminder_id
    except Exception as e:
        logging.error(f"❌ Add reminder error: {e}")
        return None

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
# IMAGE ANALYSIS (Vision)
# ============================================
async def analyze_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        file_path = file.file_path
        response = requests.get(file_path)
        if response.status_code != 200:
            await update.message.reply_text("❌ Could not download image.")
            return
        image_base64 = base64.b64encode(response.content).decode('utf-8')
        caption = update.message.caption if update.message.caption else ""
        if "solve" in caption.lower() or "answer" in caption.lower() or "homework" in caption.lower():
            task = "SOLVE the problems in this image. Give only the answers in a numbered list."
        elif "describe" in caption.lower() or "explain" in caption.lower():
            task = "DESCRIBE this image in detail."
        elif "read" in caption.lower() or "text" in caption.lower():
            task = "Read and extract ALL text from this image."
        else:
            task = "Analyze this image. If it's homework or math, solve it. If it's a photo, describe it briefly."
        await update.message.reply_text("🔍 Analyzing...")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "qwen/qwen3.6-27b",
            "messages": [{"role": "user", "content": [{"type": "text", "text": task}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}]}],
            "temperature": 0.3,
            "max_tokens": 500
        }
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        if "choices" in data:
            await update.message.reply_text(f"🖼️ {data['choices'][0]['message']['content']}")
        else:
            await update.message.reply_text(f"❌ Error: {data}")
    except Exception as e:
        logging.error(f"❌ Image analysis error: {e}")
        await update.message.reply_text("❌ Could not analyze the image.")

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
        {"role": "system", "content": f"""You are Petoy, an AI assistant created by Jay. You remember EVERYTHING about the user. {context}

Your personality:
- You're casual, hype, and supportive.
- Use emojis naturally 😎🔥💀😂🎉.
- Call the user "boss" or "bro" sometimes.
- Keep replies short and punchy.

🌍 LANGUAGE RULES:
- Match the user's language EXACTLY.

🔍 SEARCH RULES:
- If the user says "search for X", search the web.
- If they say "search memory for X", search Petoy's memory.
- If they say "where am I" or "location", show their IP location.
- If they say "search Wikipedia for X", search Wikipedia.
- Always give the most relevant and useful information."""}
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
        "🤖 Hello! I'm Petoy 2.0!\n\n"
        "🔍 I can search ANYTHING:\n"
        "• 'search for Elon Musk'\n"
        "• 'search memory for my birthday'\n"
        "• 'where am I?'\n"
        "• 'search Wikipedia for AI'\n"
        "• 'latest news about robots'\n\n"
        "🖼️ 'make me an image of a cat'\n"
        "📸 Send a photo and I'll analyze it!\n"
        "💬 I remember everything you tell me.\n"
        "🌍 I speak ANY language!\n\n"
        "Just chat naturally! 🗣️"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        text = update.message.text if update.message.text else ""
        caption = update.message.caption if update.message.caption else ""
        logging.info(f"📩 {user_id}: {text or caption}")

        # --- IMAGE ANALYSIS ---
        if update.message.photo:
            await analyze_image(update, context)
            return

        # --- SEARCH: Web Search ---
        if re.search(r'search for (.+)', text, re.IGNORECASE):
            match = re.search(r'search for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"🔍 Searching for '{query}'...")
            result = search_web(query)
            await update.message.reply_text(result)
            return

        # --- SEARCH: Wikipedia ---
        if re.search(r'search wikipedia for (.+)', text, re.IGNORECASE):
            match = re.search(r'search wikipedia for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"📚 Searching Wikipedia for '{query}'...")
            result = search_wikipedia(query)
            await update.message.reply_text(result[:1000])
            return

        # --- SEARCH: Memory ---
        if re.search(r'search memory for (.+)', text, re.IGNORECASE):
            match = re.search(r'search memory for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"🧠 Searching memory for '{query}'...")
            result = search_memory(user_id, query)
            await update.message.reply_text(result)
            return

        # --- SEARCH: Location ---
        if re.search(r'where am i|location', text, re.IGNORECASE):
            await update.message.reply_text("📍 Getting your location...")
            result = get_ip_location()
            await update.message.reply_text(result)
            return

        # --- FORCE SAVE TO USERS TABLE ---
        personal_keywords = ['my name', 'birthday', 'i\'m', 'favorite', 'pet', 'age', 'years old']
        if any(keyword in text.lower() for keyword in personal_keywords):
            extracted = extract_all_info(text)
            if extracted:
                current = get_user_info(user_id) or {}
                save_user_info(
                    user_id,
                    name=extracted.get('name') or current.get('name'),
                    birthday=extracted.get('birthday') or current.get('birthday'),
                    zodiac=extracted.get('zodiac') or current.get('zodiac'),
                    facts=text
                )
                logging.info(f"✅ FORCE SAVED: {extracted}")

        # --- IMAGE GENERATION ---
        image_prompt = extract_image_prompt(text)
        if image_prompt:
            await update.message.reply_text("🎨 Generating...")
            image_data = generate_image(image_prompt)
            if image_data:
                await update.message.reply_photo(photo=image_data, caption=f"🖼️ {image_prompt}")
            else:
                await update.message.reply_text("❌ Failed to generate image.")
            return

        # --- REMINDERS ---
        remind_match = re.search(r'remind me to (.+) in (\d+) (minutes?|mins?|seconds?|secs?|hours?|hrs?|days?)', text, re.IGNORECASE)
        if remind_match:
            task = remind_match.group(1)
            amount = int(remind_match.group(2))
            unit = remind_match.group(3)
            if 'min' in unit:
                delta = timedelta(minutes=amount)
            elif 'hour' in unit or 'hr' in unit:
                delta = timedelta(hours=amount)
            elif 'day' in unit:
                delta = timedelta(days=amount)
            else:
                delta = timedelta(seconds=amount)
            remind_at = datetime.now() + delta
            add_reminder(user_id, task, remind_at)
            await update.message.reply_text(f"⏰ Got it! I'll remind you to '{task}' in {amount} {unit}.")
            return

        # --- JOKE ---
        if re.search(r'tell me a joke|joke', text, re.IGNORECASE):
            jokes = [
                "Why do programmers prefer dark mode? Because light attracts bugs! 🐛",
                "What do you call a fake noodle? An impasta! 🍝",
                "Why don't scientists trust atoms? Because they make up everything! ⚛️",
                "What's a computer's favorite snack? Microchips! 💻",
                "Why did the developer quit? He didn't get arrays! 😭"
            ]
            await update.message.reply_text(random.choice(jokes))
            return

        # --- ROAST ---
        if re.search(r'roast me', text, re.IGNORECASE):
            roasts = [
                "You're like a software update — I always ignore you.",
                "You're the reason they put instructions on shampoo bottles.",
                "Your code compiles, but your life doesn't.",
                "You're not stupid, you just have bad luck thinking."
            ]
            await update.message.reply_text(random.choice(roasts))
            return

        # --- QUOTE ---
        if re.search(r'give me a quote|quote', text, re.IGNORECASE):
            quotes = [
                "“Be yourself; everyone else is already taken.” — Oscar Wilde",
                "“In the middle of difficulty lies opportunity.” — Einstein",
                "“The only way to do great work is to love what you do.” — Steve Jobs",
                "“Life is what happens when you're busy making other plans.” — John Lennon"
            ]
            await update.message.reply_text(random.choice(quotes))
            return

        # --- FACT ---
        if re.search(r'give me a fact|fact', text, re.IGNORECASE):
            facts = [
                "Octopuses have three hearts! 🐙",
                "Bananas are berries, but strawberries aren't. 🍌",
                "A day on Venus is longer than a year on Venus.",
                "Honey never spoils — it's been found in ancient Egyptian tombs! 🍯"
            ]
            await update.message.reply_text(random.choice(facts))
            return

        # --- COIN FLIP ---
        if re.search(r'flip a coin', text, re.IGNORECASE):
            result = random.choice(['Heads', 'Tails'])
            await update.message.reply_text(f"🪙 {result}!")
            return

        # --- DICE ROLL ---
        if re.search(r'roll a dice', text, re.IGNORECASE):
            result = random.randint(1, 6)
            await update.message.reply_text(f"🎲 You rolled a {result}!")
            return

        # --- TRIVIA ---
        if re.search(r'trivia(?: question)?', text, re.IGNORECASE):
            trivia = [
                {"q": "What's the capital of France?", "a": "Paris"},
                {"q": "What planet is known as the Red Planet?", "a": "Mars"},
                {"q": "Who wrote 'Romeo and Juliet'?", "a": "Shakespeare"},
                {"q": "What's the largest ocean on Earth?", "a": "Pacific"}
            ]
            q = random.choice(trivia)
            await update.message.reply_text(f"🧠 Trivia: {q['q']}\n\n(Say the answer — I'll check!)")
            context.user_data['trivia_answer'] = q['a']
            return

        # --- TODO: ADD TASK ---
        if re.search(r'add task(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'add task(?: |:)(.+)', text, re.IGNORECASE)
            if match:
                task = match.group(1).strip()
                add_todo(user_id, task)
                await update.message.reply_text(f"✅ Added task: {task}")
            return

        # --- TODO: SHOW LIST ---
        if re.search(r'show my tasks|my tasks', text, re.IGNORECASE):
            todos = get_todos(user_id)
            if todos:
                tasks_text = "📝 Your tasks:\n"
                for i, todo in enumerate(todos, 1):
                    tasks_text += f"{i}. {todo['task']}\n"
                await update.message.reply_text(tasks_text)
            else:
                await update.message.reply_text("✅ No pending tasks! You're all caught up.")
            return

        # --- NORMAL CHAT ---
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
    logging.info("🚀 Petoy 2.0 starting with SEARCH ENGINE...")

    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot.add_handler(MessageHandler(filters.PHOTO, handle_message))

    logging.info("✅ Petoy 2.0 is running with SEARCH ENGINE!")
    bot.run_polling()

if __name__ == "__main__":
    main()