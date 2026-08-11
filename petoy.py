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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
AGNES_API_KEY = os.environ.get("AGNES_API_KEY")

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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
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
        logging.error(f"❌ Save note error: {e}")
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
        logging.error(f"❌ Get notes error: {e}")
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
        logging.error(f"❌ Search note error: {e}")
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
        logging.error(f"❌ Delete note error: {e}")
        return False

# ============================================
# 🔍 SEARCH ENGINE
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
# 🎬 VIDEO GENERATION (Agnes AI — 5 Minute Timeout)
# ============================================
def create_video_task(prompt):
    if not AGNES_API_KEY:
        logging.error("❌ AGNES_API_KEY is not set!")
        return None
    
    url = "https://apihub.agnes-ai.com/v1/videos"
    headers = {
        "Authorization": f"Bearer {AGNES_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "prompt": prompt,
        "model": "agnes-video-v2.0",
        "aspect_ratio": "16:9",
        "duration": 5,
        "frames": 60,
    }
    
    try:
        logging.info(f"📡 Sending video request: {prompt[:50]}...")
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        logging.info(f"📡 Response status: {response.status_code}")
        logging.info(f"📡 Response body: {response.text[:300]}")
        
        if response.status_code != 200:
            logging.error(f"❌ API error: {response.status_code}")
            return None
            
        data = response.json()
        return data.get("video_id") or data.get("id")
    except Exception as e:
        logging.error(f"❌ Video creation error: {e}")
        return None

def poll_video_status(video_id):
    """Poll video status for up to 5 minutes"""
    url = "https://apihub.agnes-ai.com/agnesapi"
    params = {"video_id": video_id}
    headers = {"Authorization": f"Bearer {AGNES_API_KEY}"}
    
    for attempt in range(60):  # 60 attempts × 5 seconds = 5 minutes
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            data = response.json()
            status = str(data.get("status", "")).lower()
            progress = data.get("progress", 0)
            logging.info(f"📡 Poll {attempt+1}: status={status}, progress={progress}%")
            
            if status in {"succeeded", "success", "completed", "done"}:
                video_url = data.get("video_url") or data.get("url")
                if video_url:
                    return {"success": True, "url": video_url}
                return {"success": False, "error": "No video URL"}
            if status in {"failed", "error", "cancelled"}:
                return {"success": False, "error": data}
        except Exception as e:
            logging.error(f"❌ Poll error: {e}")
        time.sleep(5)
    
    return {"success": False, "error": "Timed out after 5 minutes"}

def generate_fallback_image(prompt):
    try:
        url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=512&height=512&nologo=true"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.content
    except:
        pass
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
# IMAGE ANALYSIS
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

📌 REMEMBER:
- If the user says they don't want to talk about something, NEVER bring it up again.

🔍 FEATURES:
- Search 20+ sources
- Save and recall notes
- Math solver, unit converter
- Time in cities, countdowns
- Brainstorm, pros/cons, would you rather
- Image generation and analysis
- Video generation with fallback to images"""}
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
        "🤖 Hello! I'm Petoy 2.0!\n\n"
        "🔍 **Search:** 'search for Elon Musk'\n"
        "📝 **Notes:** 'save note: meeting at 3pm'\n"
        "🧮 **Math:** 'solve 2+2'\n"
        "📏 **Convert:** 'convert 10 km to miles'\n"
        "🕐 **Time:** 'time in Tokyo'\n"
        "⏳ **Countdown:** 'countdown to December 25'\n"
        "💡 **Brainstorm:** 'brainstorm: startup ideas'\n"
        "🎬 **Video:** 'make a video of a cat walking'\n"
        "🖼️ **Image:** 'make me an image of a cat'\n"
        "📸 **Send a photo** and I'll analyze it!\n\n"
        "🌍 I speak ANY language! Just chat naturally! 🗣️"
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

        # --- VIDEO GENERATION ---
        video_keywords = [
            r'make a video of (.+)',
            r'generate a video of (.+)',
            r'create a video of (.+)',
            r'video of (.+)',
            r'make me a video of (.+)',
            r'generate me a video of (.+)',
            r'create me a video of (.+)'
        ]
        
        video_match = None
        for pattern in video_keywords:
            video_match = re.search(pattern, text, re.IGNORECASE)
            if video_match:
                break
        
        if video_match:
            prompt = video_match.group(1).strip()
            
            if not AGNES_API_KEY:
                await update.message.reply_text("❌ Video API key not configured.")
                return
            
            if not prompt:
                await update.message.reply_text("⚠️ What kind of video do you want?")
                return
            
            await update.message.reply_text("🎬 Generating your video... this may take 1-3 minutes.")
            
            video_id = create_video_task(prompt)
            if not video_id:
                await update.message.reply_text("⚠️ Video generation failed. Try again.")
                return
            
            result = poll_video_status(video_id)
            
            if result["success"] and result.get("url"):
                video_url = result["url"]
                
                # Check if URL works
                try:
                    head_response = requests.head(video_url, timeout=5)
                    if head_response.status_code != 200:
                        await update.message.reply_text("❌ Video was generated but the URL is broken. Try again.")
                        return
                except:
                    await update.message.reply_text("❌ Video URL is unreachable. Try again.")
                    return
                
                await update.message.reply_video(video_url, caption=f"🎥 Here's your video: {prompt}")
            else:
                await update.message.reply_text(f"❌ Video generation failed: {result.get('error', 'Unknown error')}")
                # Fallback to image
                image_data = generate_fallback_image(prompt)
                if image_data:
                    await update.message.reply_photo(
                        photo=image_data,
                        caption=f"🖼️ Here's an image for: {prompt}\n(Video is temporarily unavailable)"
                    )
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
    bot.add_handler(MessageHandler(filters.PHOTO, handle_message))

    logging.info("✅ Petoy 2.0 is running!")
    bot.run_polling()

if __name__ == "__main__":
    main()