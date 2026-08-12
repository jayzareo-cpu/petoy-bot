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
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, DATABASE_URL]):
    logger.error("❌ Missing environment variables!")
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_topics (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS timers (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                timer_at TIMESTAMP NOT NULL,
                done BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("✅ Database ready")
    except Exception as e:
        logger.error(f"❌ DB init error: {e}")

init_db()

# ============================================
# BLOCKED TOPICS
# ============================================
def block_topic(user_id, topic):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO blocked_topics (user_id, topic) VALUES (%s, %s)",
            (user_id, topic.lower())
        )
        conn.commit()
        conn.close()
        logger.info(f"🚫 Blocked topic '{topic}' for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Block topic error: {e}")
        return False

def get_blocked_topics(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT topic FROM blocked_topics WHERE user_id = %s",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [row["topic"] for row in rows]
    except Exception as e:
        logger.error(f"❌ Get blocked topics error: {e}")
        return []

# ============================================
# TIMER FUNCTIONS
# ============================================
def add_timer(user_id, message, timer_at):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO timers (user_id, message, timer_at) VALUES (%s, %s, %s) RETURNING id",
            (user_id, message, timer_at)
        )
        timer_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        logger.info(f"⏰ Timer saved: {message} at {timer_at}")
        return timer_id
    except Exception as e:
        logger.error(f"❌ Add timer error: {e}")
        return None

def get_timers(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT id, message, timer_at FROM timers WHERE user_id = %s AND done = FALSE ORDER BY timer_at",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"❌ Get timers error: {e}")
        return []

def delete_timer(user_id, timer_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM timers WHERE user_id = %s AND id = %s",
            (user_id, timer_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Delete timer error: {e}")
        return False

# ============================================
# REMINDER FUNCTIONS
# ============================================
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
        logger.info(f"🔔 Reminder saved: {message} at {remind_at}")
        return reminder_id
    except Exception as e:
        logger.error(f"❌ Add reminder error: {e}")
        return None

def get_reminders(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT id, message, remind_at FROM reminders WHERE user_id = %s AND done = FALSE ORDER BY remind_at",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"❌ Get reminders error: {e}")
        return []

def delete_reminder(user_id, reminder_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM reminders WHERE user_id = %s AND id = %s",
            (user_id, reminder_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Delete reminder error: {e}")
        return False

# ============================================
# BACKGROUND TIMER CHECK
# ============================================
def check_timers_and_reminders(bot):
    """Background thread to check for due timers and reminders"""
    while True:
        try:
            # Check timers
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                "SELECT id, user_id, message FROM timers WHERE timer_at <= NOW() AND done = FALSE"
            )
            due_timers = cursor.fetchall()
            
            for timer in due_timers:
                logger.info(f"⏰ Timer due for user {timer['user_id']}: {timer['message']}")
                # Mark as done
                cursor2 = conn.cursor()
                cursor2.execute(
                    "UPDATE timers SET done = TRUE WHERE id = %s",
                    (timer['id'],)
                )
                cursor2.close()
                # Send reminder via bot
                try:
                    bot.send_message(chat_id=timer['user_id'], text=f"⏰ **Timer is up!** {timer['message']}")
                except:
                    pass
            
            # Check reminders
            cursor.execute(
                "SELECT id, user_id, message FROM reminders WHERE remind_at <= NOW() AND done = FALSE"
            )
            due_reminders = cursor.fetchall()
            
            for reminder in due_reminders:
                logger.info(f"🔔 Reminder due for user {reminder['user_id']}: {reminder['message']}")
                cursor2 = conn.cursor()
                cursor2.execute(
                    "UPDATE reminders SET done = TRUE WHERE id = %s",
                    (reminder['id'],)
                )
                cursor2.close()
                try:
                    bot.send_message(chat_id=reminder['user_id'], text=f"🔔 **Reminder!** {reminder['message']}")
                except:
                    pass
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Check timers/reminders error: {e}")
        time.sleep(30)  # Check every 30 seconds

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
        logger.error(f"❌ Save message error: {e}")

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
        logger.error(f"❌ History error: {e}")
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
        logger.error(f"❌ Save user error: {e}")

def get_user_info(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        logger.error(f"❌ Get user error: {e}")
        return None

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
        logger.error(f"❌ Add todo error: {e}")
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
        logger.error(f"❌ Get todos error: {e}")
        return []

def save_note(user_id, title, content):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notes (user_id, title, content) VALUES (%s, %s, %s) RETURNING id",
            (user_id, title, content)
        )
        note_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return note_id
    except Exception as e:
        logger.error(f"❌ Save note error: {e}")
        return None

def get_notes(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT id, title, content, created_at FROM notes WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"❌ Get notes error: {e}")
        return []

def search_note(user_id, query):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT id, title, content FROM notes WHERE user_id = %s AND (title ILIKE %s OR content ILIKE %s) ORDER BY created_at DESC",
            (user_id, f"%{query}%", f"%{query}%")
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"❌ Search note error: {e}")
        return []

def delete_note(user_id, note_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM notes WHERE user_id = %s AND id = %s",
            (user_id, note_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Delete note error: {e}")
        return False

# ============================================
# SEARCH ENGINE
# ============================================
def search_web(query):
    results = []
    query_encoded = query.replace(' ', '+')
    
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
        r = requests.get(url, timeout=6)
        data = r.json()
        if data.get("AbstractText"):
            results.append(f"📌 DuckDuckGo: {data['AbstractText'][:500]}")
        if data.get("Answer"):
            results.append(f"💡 {data['Answer']}")
        if data.get("Definition"):
            results.append(f"📖 {data['Definition']}")
    except:
        pass

    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        r = requests.get(url, timeout=6)
        data = r.json()
        if data.get("extract"):
            results.append(f"📚 Wikipedia: {data['extract'][:500]}")
    except:
        pass

    try:
        url = f"https://api.github.com/search/repositories?q={query}&per_page=2"
        r = requests.get(url, timeout=6, headers={"User-Agent": "PetoyBot"})
        if r.status_code == 200:
            data = r.json()
            for repo in data.get("items", [])[:2]:
                desc = repo.get('description', 'No description')
                results.append(f"🐙 GitHub: {repo['name']} — {desc[:200]}")
    except:
        pass

    try:
        url = f"https://www.reddit.com/r/all/search.json?q={query}&limit=2"
        r = requests.get(url, timeout=6, headers={"User-Agent": "PetoyBot"})
        if r.status_code == 200:
            data = r.json()
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                title = post_data.get("title", "No title")
                subreddit = post_data.get("subreddit", "unknown")
                results.append(f"🔴 Reddit: r/{subreddit} — {title[:200]}")
    except:
        pass

    try:
        url = f"https://api.stackexchange.com/2.3/search?order=desc&sort=relevance&intitle={query}&site=stackoverflow&pagesize=2"
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("items", []):
                results.append(f"💻 Stack Overflow: {item['title'][:300]}")
    except:
        pass

    try:
        url = f"https://www.youtube.com/results?search_query={query_encoded}"
        r = requests.get(url, timeout=6)
        titles = re.findall(r'video-title">(.*?)<', r.text)
        for title in titles[:2]:
            results.append(f"📹 YouTube: {title[:200]}")
    except:
        pass

    try:
        url = "https://feeds.bbci.co.uk/news/rss.xml"
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:3]:
            title = item.find("title").text if item.find("title") is not None else ""
            if query.lower() in title.lower():
                results.append(f"📰 BBC News: {title[:200]}")
    except:
        pass

    try:
        url = "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:3]:
            title = item.find("title").text if item.find("title") is not None else ""
            if query.lower() in title.lower():
                results.append(f"📰 NY Times: {title[:200]}")
    except:
        pass

    try:
        url = f"https://api.urbandictionary.com/v0/define?term={query}"
        r = requests.get(url, timeout=6)
        data = r.json()
        if data.get("list"):
            definition = data["list"][0].get("definition", "")
            results.append(f"📖 Urban Dictionary: {definition[:300]}")
    except:
        pass

    if not results:
        return f"🔍 No results found for '{query}'. Try being more specific."
    
    seen = set()
    unique_results = []
    for result in results:
        if result[:50] not in seen:
            seen.add(result[:50])
            unique_results.append(result)
    return "\n\n".join(unique_results[:6])

def search_wikipedia(query):
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        r = requests.get(url, timeout=8)
        data = r.json()
        if data.get("extract"):
            return f"📚 Wikipedia: {data['extract'][:800]}"
        return f"❌ No Wikipedia page found for '{query}'"
    except:
        return f"❌ Could not reach Wikipedia"

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
# IMAGE ANALYSIS — ALL FIXES
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
        caption_lower = caption.lower()

        # --- NEGATIVE COMMANDS ---
        negative_keywords = [
            "don't solve", "dont solve", "no solve", "not solve", "do not solve",
            "don't describe", "dont describe", "no describe", "not describe", "do not describe",
            "don't read", "dont read", "no read", "not read", "do not read",
            "don't analyze", "dont analyze", "no analyze", "not analyze", "do not analyze",
            "don't explain", "dont explain", "no explain", "not explain", "do not explain",
            "don't answer", "dont answer", "no answer", "not answer", "do not answer",
            "don't tell", "dont tell", "no tell", "not tell", "do not tell",
            "no math", "no description", "no analysis", "no explanation",
            "skip solve", "skip describe", "skip read", "skip analysis",
            "ignore solve", "ignore describe", "ignore read", "ignore analysis",
            "cancel solve", "cancel describe", "cancel read", "cancel analysis"
        ]
        
        for neg in negative_keywords:
            if neg in caption_lower:
                await update.message.reply_text("✅ Got it, boss. I'll do what you asked.")
                return

        # --- LOCATION / PLACE DETECTION ---
        location_keywords = ["where is this", "where do you think this is", "what place is this", "location", "place"]
        if any(keyword in caption_lower for keyword in location_keywords):
            location_prompt = """You are a location analyzer. Guess where this image was taken.

RULES:
- Look for clues like landmarks, signs, architecture, language, nature, weather, culture.
- Make your best guess.
- If unsure, say "I think this might be..." or "This looks like..."
- DO NOT describe the image in detail.
- DO NOT solve anything.
- ONLY give your location guess.
- Be brief, 2-3 sentences max.
- Plain text only. No LaTeX. No personality."""

            await update.message.reply_text("📍 Figuring out where this is...")
            
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "qwen/qwen3.6-27b",
                "messages": [
                    {"role": "system", "content": "You are a location analyzer. Plain text only. No LaTeX. No personality."},
                    {"role": "user", "content": [
                        {"type": "text", "text": location_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                    ]}
                ],
                "temperature": 0.3,
                "max_tokens": 150
            }
            response = requests.post(url, headers=headers, json=payload)
            data = response.json()
            if "choices" in data:
                reply = data["choices"][0]["message"]["content"]
                reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL)
                reply = re.sub(r'\$\\frac\{(\d+)\}\{(\d+)\}\$', r'\1/\2', reply)
                reply = re.sub(r'\\\[|\\\]|\\\(|\\\)', '', reply)
                reply = re.sub(r'(?i)boss.*', '', reply)
                reply = re.sub(r'(?i)bro.*', '', reply)
                reply = reply.strip()
                await update.message.reply_text(f"📍 {reply}")
            else:
                await update.message.reply_text(f"❌ Error: {data}")
            return

        # --- INSTRUCTIONS MODE ---
        if "teach me" in caption_lower or "show steps" in caption_lower or "instructions" in caption_lower:
            task = """Teach the user how to solve these problems. Show step-by-step instructions.
            Explain how to add fractions with like denominators.
            Use clear, simple language.
            Plain text only. No LaTeX."""
            mode = "📚 Teaching..."
        else:
            # --- ULTRA STRICT MATH SOLVER ---
            if "solve" in caption_lower or "homework" in caption_lower or "math" in caption_lower:
                math_prompt = """You are a math calculator. ONLY output answers.

FORMAT:
1) 5/6
2) 5/9
3) 11/8
4) 4/3
5) 7/5
6) 3/4

RULES:
- NO "Boss", "bro", "I love you"
- NO explanations, NO steps, NO "Final answer?"
- NO <think> tags
- NO LaTeX
- Plain text only.
- If 6 problems, give 6 answers."""

                await update.message.reply_text("📐 Solving...")
                
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": "qwen/qwen3.6-27b",
                    "messages": [
                        {"role": "system", "content": "You are a calculator. Plain text only. No LaTeX. No personality."},
                        {"role": "user", "content": [
                            {"type": "text", "text": math_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                        ]}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 200
                }
                response = requests.post(url, headers=headers, json=payload)
                data = response.json()
                if "choices" in data:
                    reply = data["choices"][0]["message"]["content"]
                    reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL)
                    reply = re.sub(r'\$\\frac\{(\d+)\}\{(\d+)\}\$', r'\1/\2', reply)
                    reply = re.sub(r'\\\[|\\\]|\\\(|\\\)', '', reply)
                    reply = re.sub(r'(?i)boss.*', '', reply)
                    reply = re.sub(r'(?i)bro.*', '', reply)
                    reply = re.sub(r'(?i)final answer\??.*', '', reply)
                    reply = reply.strip()
                    await update.message.reply_text(f"📐 {reply}")
                else:
                    await update.message.reply_text(f"❌ Error: {data}")
                return

            # --- DESCRIPTION ---
            if "describe" in caption_lower or "explain" in caption_lower:
                task = "DESCRIBE this image in detail. Focus on what you see. Plain text. No LaTeX."
                mode = "🔍 Describing..."
            # --- READ TEXT ---
            elif "read" in caption_lower or "text" in caption_lower:
                task = "Read and extract ALL text from this image. Plain text only."
                mode = "📖 Reading text..."
            else:
                task = "Analyze this image. If it's math, solve it. If it's a photo, describe it. Plain text. No LaTeX."
                mode = "🔍 Analyzing..."

        await update.message.reply_text(mode)
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
            reply = data["choices"][0]["message"]["content"]
            reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL)
            reply = re.sub(r'\$\\frac\{(\d+)\}\{(\d+)\}\$', r'\1/\2', reply)
            reply = re.sub(r'\\\[|\\\]|\\\(|\\\)', '', reply)
            reply = reply.strip()
            await update.message.reply_text(f"🖼️ {reply}")
        else:
            await update.message.reply_text(f"❌ Error: {data}")
    except Exception as e:
        logger.error(f"❌ Image analysis error: {e}")
        await update.message.reply_text("❌ Could not analyze the image.")

# ============================================
# GROQ AI WITH BLOCKED TOPICS
# ============================================
def ask_groq(user_id, question):
    blocked = get_blocked_topics(user_id)
    user_mentioned_blocked = any(topic in question.lower() for topic in blocked)
    
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

🚫 TOPIC RULES:
- NEVER bring up blocked topics on your own.
- If the user mentions a blocked topic, you can reply briefly.
- If the user says "stop talking about X", block that topic.

🌍 LANGUAGE RULES:
- Match the user's language EXACTLY.

🔍 FEATURES:
- Search 20+ sources
- Save and recall notes
- To-do list and reminders
- Timers and countdowns
- Math solver and unit converter
- Time in cities and countdowns
- Brainstorm and pros/cons
- Would you rather, palindrome check, reverse text
- Image generation and analysis"""}
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
        logger.error(f"❌ Groq error: {e}")
        return "Error. Please try again."

# ============================================
# EXTRACT INFO
# ============================================
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

# ============================================
# TELEGRAM HANDLERS
# ============================================
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CommandHandler, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 **Petoy 2.0**\n\n"
        "📸 **Send a photo with a caption:**\n"
        "• 'solve' — get only the answers\n"
        "• 'teach me' — get step-by-step instructions\n"
        "• 'describe' — get a description\n"
        "• 'where is this' — guess the location\n"
        "• 'don't solve' — cancel math solving\n\n"
        "⏰ **Timers & Reminders:**\n"
        "• 'set a timer for 5 minutes'\n"
        "• 'remind me to call mom in 10 minutes'\n"
        "• 'show timers' — list active timers\n"
        "• 'show reminders' — list active reminders\n"
        "• 'cancel timer 1' — cancel timer #1\n"
        "• 'cancel reminder 1' — cancel reminder #1\n\n"
        "🧠 I learn from my mistakes! Say 'stop talking about [topic]' to block it.\n\n"
        "🔍 'search for Elon Musk'\n"
        "📝 'save note: meeting at 3pm'\n"
        "🖼️ 'make me an image of a cat'\n"
        "🌍 I speak ANY language!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        text = update.message.text if update.message.text else ""
        caption = update.message.caption if update.message.caption else ""
        logger.info(f"📩 {user_id}: {text or caption}")

        # --- BLOCK TOPIC ---
        if re.search(r'stop talking about (.+)', text, re.IGNORECASE):
            match = re.search(r'stop talking about (.+)', text, re.IGNORECASE)
            topic = match.group(1).strip().lower()
            block_topic(user_id, topic)
            await update.message.reply_text(f"✅ Got it, boss. I'll never mention '{topic}' again. 🔇")
            return

        # --- IMAGE ANALYSIS ---
        if update.message.photo:
            await analyze_image(update, context)
            return

        # --- TIMER: SET ---
        timer_match = re.search(r'set a timer for (\d+) minutes?', text, re.IGNORECASE)
        if timer_match:
            minutes = int(timer_match.group(1))
            timer_at = datetime.now() + timedelta(minutes=minutes)
            add_timer(user_id, f"Timer for {minutes} minutes", timer_at)
            await update.message.reply_text(f"⏰ Timer set for {minutes} minutes. I'll remind you at {timer_at.strftime('%I:%M %p')}!")
            return

        # --- TIMER: SHOW ---
        if re.search(r'show timers', text, re.IGNORECASE):
            timers = get_timers(user_id)
            if timers:
                timer_list = "⏰ **Your active timers:**\n"
                for i, timer in enumerate(timers, 1):
                    timer_list += f"{i}. {timer['message']} at {timer['timer_at'].strftime('%I:%M %p')}\n"
                await update.message.reply_text(timer_list)
            else:
                await update.message.reply_text("⏰ No active timers.")
            return

        # --- TIMER: CANCEL ---
        cancel_timer_match = re.search(r'cancel timer (\d+)', text, re.IGNORECASE)
        if cancel_timer_match:
            timer_id = int(cancel_timer_match.group(1))
            if delete_timer(user_id, timer_id):
                await update.message.reply_text(f"✅ Timer {timer_id} cancelled.")
            else:
                await update.message.reply_text("❌ Could not cancel timer.")
            return

        # --- REMINDER: SET ---
        reminder_match = re.search(r'remind me to (.+) in (\d+) minutes?', text, re.IGNORECASE)
        if reminder_match:
            message = reminder_match.group(1)
            minutes = int(reminder_match.group(2))
            remind_at = datetime.now() + timedelta(minutes=minutes)
            add_reminder(user_id, message, remind_at)
            await update.message.reply_text(f"🔔 Reminder set: '{message}' in {minutes} minutes at {remind_at.strftime('%I:%M %p')}!")
            return

        # --- REMINDER: SHOW ---
        if re.search(r'show reminders', text, re.IGNORECASE):
            reminders = get_reminders(user_id)
            if reminders:
                reminder_list = "🔔 **Your active reminders:**\n"
                for i, reminder in enumerate(reminders, 1):
                    reminder_list += f"{i}. {reminder['message']} at {reminder['remind_at'].strftime('%I:%M %p')}\n"
                await update.message.reply_text(reminder_list)
            else:
                await update.message.reply_text("🔔 No active reminders.")
            return

        # --- REMINDER: CANCEL ---
        cancel_reminder_match = re.search(r'cancel reminder (\d+)', text, re.IGNORECASE)
        if cancel_reminder_match:
            reminder_id = int(cancel_reminder_match.group(1))
            if delete_reminder(user_id, reminder_id):
                await update.message.reply_text(f"✅ Reminder {reminder_id} cancelled.")
            else:
                await update.message.reply_text("❌ Could not cancel reminder.")
            return

        # --- SEARCH ---
        if re.search(r'search for (.+)', text, re.IGNORECASE):
            match = re.search(r'search for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"🔍 Searching for '{query}'...")
            result = search_web(query)
            await update.message.reply_text(result[:4000])
            return

        if re.search(r'search wikipedia for (.+)', text, re.IGNORECASE):
            match = re.search(r'search wikipedia for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"📚 Searching Wikipedia for '{query}'...")
            result = search_wikipedia(query)
            await update.message.reply_text(result[:1000])
            return

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

        # --- NOTES ---
        if re.search(r'save note(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'save note(?: |:)(.+)', text, re.IGNORECASE)
            content = match.group(1).strip()
            title = content[:30] + "..." if len(content) > 30 else content
            save_note(user_id, title, content)
            await update.message.reply_text(f"✅ Note saved: '{title}'")
            return

        if re.search(r'recall note(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'recall note(?: |:)(.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            results = search_note(user_id, query)
            if results:
                result = results[0]
                await update.message.reply_text(f"📝 **{result['title']}**\n\n{result['content']}")
            else:
                await update.message.reply_text(f"❌ No note found for '{query}'")
            return

        if re.search(r'show notes', text, re.IGNORECASE):
            notes = get_notes(user_id)
            if notes:
                note_list = "📝 **Your Notes:**\n"
                for i, note in enumerate(notes[:10], 1):
                    note_list += f"{i}. {note['title']}\n"
                await update.message.reply_text(note_list)
            else:
                await update.message.reply_text("📝 No notes saved yet.")
            return

        if re.search(r'delete note(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'delete note(?: |:)(.+)', text, re.IGNORECASE)
            title = match.group(1).strip()
            notes = search_note(user_id, title)
            if notes:
                delete_note(user_id, notes[0]['id'])
                await update.message.reply_text(f"✅ Deleted note: '{title}'")
            else:
                await update.message.reply_text(f"❌ No note found for '{title}'")
            return

        # --- MATH ---
        if re.search(r'solve (.+)', text, re.IGNORECASE):
            match = re.search(r'solve (.+)', text, re.IGNORECASE)
            expr = match.group(1).strip()
            try:
                result = eval(expr)
                await update.message.reply_text(f"✅ {expr} = {result}")
            except:
                await update.message.reply_text("❌ Could not solve that. Try: 2 + 2")
            return

        # --- CONVERT ---
        if re.search(r'convert (\d+\.?\d*) (\w+) to (\w+)', text, re.IGNORECASE):
            match = re.search(r'convert (\d+\.?\d*) (\w+) to (\w+)', text, re.IGNORECASE)
            value = float(match.group(1))
            from_unit = match.group(2).lower()
            to_unit = match.group(3).lower()
            conversions = {
                'km_to_miles': 0.621371,
                'miles_to_km': 1.60934,
                'kg_to_lbs': 2.20462,
                'lbs_to_kg': 0.453592,
            }
            key = f"{from_unit}_to_{to_unit}"
            if key in conversions:
                result = value * conversions[key]
                await update.message.reply_text(f"🔄 {value} {from_unit} = {result:.2f} {to_unit}")
            else:
                await update.message.reply_text("❌ Conversion not supported")
            return

        # --- TIME ---
        if re.search(r'time in (\w+)', text, re.IGNORECASE):
            match = re.search(r'time in (\w+)', text, re.IGNORECASE)
            city = match.group(1).strip()
            timezones = {
                'tokyo': 9, 'new york': -5, 'london': 0, 'manila': 8,
                'sydney': 11, 'dubai': 4, 'singapore': 8, 'paris': 1,
                'berlin': 1, 'rome': 1, 'los angeles': -8, 'chicago': -6,
            }
            city_lower = city.lower()
            if city_lower in timezones:
                offset = timezones[city_lower]
                utc_now = datetime.utcnow()
                local_time = utc_now + timedelta(hours=offset)
                await update.message.reply_text(f"🕐 Time in {city.title()}: {local_time.strftime('%I:%M %p')}")
            else:
                await update.message.reply_text(f"❌ No timezone info for {city}")
            return

        # --- COUNTDOWN ---
        if re.search(r'countdown to (.+)', text, re.IGNORECASE):
            match = re.search(r'countdown to (.+)', text, re.IGNORECASE)
            date_str = match.group(1).strip()
            try:
                target = datetime.strptime(date_str, '%B %d')
                target = target.replace(year=datetime.now().year)
                if target < datetime.now():
                    target = target.replace(year=datetime.now().year + 1)
                days = (target - datetime.now()).days
                await update.message.reply_text(f"⏳ {days} days until {date_str}!")
            except:
                await update.message.reply_text("❌ Use format: 'countdown to December 25'")
            return

        # --- BRAINSTORM ---
        if re.search(r'brainstorm(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'brainstorm(?: |:)(.+)', text, re.IGNORECASE)
            topic = match.group(1).strip()
            ideas = [
                f"💡 1. {topic} with AI integration",
                f"💡 2. {topic} for everyday use",
                f"💡 3. {topic} that solves a common problem",
                f"💡 4. {topic} using open-source tools",
                f"💡 5. {topic} with a subscription model",
                f"💡 6. {topic} for mobile first",
                f"💡 7. {topic} with gamification",
                f"💡 8. {topic} that connects people",
                f"💡 9. {topic} with automation",
                f"💡 10. {topic} that makes life easier",
            ]
            await update.message.reply_text("\n".join(ideas))
            return

        # --- PROS & CONS ---
        if re.search(r'pros and cons of (.+)', text, re.IGNORECASE):
            match = re.search(r'pros and cons of (.+)', text, re.IGNORECASE)
            topic = match.group(1).strip()
            pros = ["✅ Easy to start", "✅ Low cost", "✅ High demand", "✅ Scalable", "✅ Fun to work on"]
            cons = ["❌ Takes time", "❌ Requires learning", "❌ Competition", "❌ Need patience", "❌ Not guaranteed success"]
            await update.message.reply_text(f"📊 **Pros and Cons of {topic}**\n\nPros:\n" + "\n".join(pros) + "\n\nCons:\n" + "\n".join(cons))
            return

        # --- WOULD YOU RATHER ---
        if re.search(r'would you rather(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'would you rather(?: |:)(.+)', text, re.IGNORECASE)
            options = match.group(1).strip().split(' or ')
            if len(options) == 2:
                await update.message.reply_text(f"🤔 Would you rather:\n1️⃣ {options[0].strip()}\n2️⃣ {options[1].strip()}\n\nChoose 1 or 2!")
            else:
                await update.message.reply_text("❌ Format: 'would you rather: pizza or burger?'")
            return

        # --- PALINDROME ---
        if re.search(r'palindrome(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'palindrome(?: |:)(.+)', text, re.IGNORECASE)
            word = match.group(1).strip()
            clean = re.sub(r'[^a-zA-Z]', '', word.lower())
            is_pal = clean == clean[::-1]
            await update.message.reply_text(f"✅ '{word}' is a palindrome!" if is_pal else f"❌ '{word}' is not a palindrome.")
            return

        # --- REVERSE ---
        if re.search(r'reverse(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'reverse(?: |:)(.+)', text, re.IGNORECASE)
            word = match.group(1).strip()
            await update.message.reply_text(f"🔄 Reversed: {word[::-1]}")
            return

        # --- FORCE SAVE ---
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
                logger.info(f"✅ FORCE SAVED: {extracted}")

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

        # --- TODO ---
        if re.search(r'add task(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'add task(?: |:)(.+)', text, re.IGNORECASE)
            if match:
                task = match.group(1).strip()
                add_todo(user_id, task)
                await update.message.reply_text(f"✅ Added task: {task}")
            return

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
        logger.error(f"❌ Error: {e}")
        await update.message.reply_text("Error. Please try again.")

# ============================================
# MAIN
# ============================================
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Start background timer checker
    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    timer_thread = threading.Thread(target=check_timers_and_reminders, args=(bot,), daemon=True)
    timer_thread.start()
    
    logger.info("🚀 Petoy 2.0 starting with TIMERS & REMINDERS...")

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot.add_handler(MessageHandler(filters.PHOTO, handle_message))

    logger.info("✅ Petoy 2.0 is running with TIMERS & REMINDERS!")
    bot.run_polling()

if __name__ == "__main__":
    main()