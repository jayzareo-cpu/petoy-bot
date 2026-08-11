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
# 🔍 SEARCH ENGINE — 20+ SOURCES
# ============================================
def search_web(query):
    """Search 20+ sources without API keys"""
    results = []
    query_encoded = query.replace(' ', '+')
    
    # 1. DuckDuckGo
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

    # 2. Wikipedia
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        r = requests.get(url, timeout=6)
        data = r.json()
        if data.get("extract"):
            results.append(f"📚 Wikipedia: {data['extract'][:500]}")
    except:
        pass

    # 3. Wiktionary
    try:
        url = f"https://en.wiktionary.org/api/rest_v1/page/definition/{query.replace(' ', '_')}"
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if data.get("en"):
                for definition in data["en"]:
                    if definition.get("definitions"):
                        results.append(f"📖 Wiktionary: {definition['definitions'][0]['definition'][:300]}")
                        break
    except:
        pass

    # 4. GitHub
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

    # 5. Reddit
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

    # 6. Stack Overflow
    try:
        url = f"https://api.stackexchange.com/2.3/search?order=desc&sort=relevance&intitle={query}&site=stackoverflow&pagesize=2"
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("items", []):
                results.append(f"💻 Stack Overflow: {item['title'][:300]}")
    except:
        pass

    # 7. YouTube
    try:
        url = f"https://www.youtube.com/results?search_query={query_encoded}"
        r = requests.get(url, timeout=6)
        titles = re.findall(r'video-title">(.*?)<', r.text)
        for title in titles[:2]:
            results.append(f"📹 YouTube: {title[:200]}")
    except:
        pass

    # 8. BBC News
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

    # 9. NY Times
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

    # 10. Urban Dictionary
    try:
        url = f"https://api.urbandictionary.com/v0/define?term={query}"
        r = requests.get(url, timeout=6)
        data = r.json()
        if data.get("list"):
            definition = data["list"][0].get("definition", "")
            results.append(f"📖 Urban Dictionary: {definition[:300]}")
    except:
        pass

    # 11. Thesaurus
    try:
        url = f"https://www.thesaurus.com/browse/{query.replace(' ', '%20')}"
        r = requests.get(url, timeout=6)
        synonyms = re.findall(r'<a class="css-1kg1xv8" href="[^"]*">([^<]+)</a>', r.text)[:3]
        if synonyms:
            results.append(f"🔤 Thesaurus: {', '.join(synonyms)}")
    except:
        pass

    # 12. Quotable (Quotes)
    try:
        url = f"https://api.quotable.io/search/phrases?query={query}"
        r = requests.get(url, timeout=6)
        data = r.json()
        for result in data.get("results", [])[:2]:
            results.append(f"💬 Quote: \"{result.get('content', '')[:200]}\"")
    except:
        pass

    # 13. Dictionary API
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{query}"
        r = requests.get(url, timeout=6)
        data = r.json()
        if isinstance(data, list) and data:
            definition = data[0].get("meanings", [{}])[0].get("definitions", [{}])[0].get("definition", "")
            if definition:
                results.append(f"📚 Dictionary: {definition[:300]}")
    except:
        pass

    # 14. IMDb
    try:
        url = f"https://v2.sg.media-imdb.com/suggestion/{query[0]}/{query}.json"
        r = requests.get(url, timeout=6)
        data = r.json()
        for item in data.get("d", [])[:2]:
            results.append(f"🎬 IMDb: {item.get('l', 'Unknown')} ({item.get('y', 'N/A')})")
    except:
        pass

    # 15. Twitter/X via Nitter
    try:
        url = f"https://nitter.poast.org/search?q={query}"
        r = requests.get(url, timeout=6)
        tweets = re.findall(r'<div class="tweet-content media-body">(.*?)</div>', r.text)[:2]
        for tweet in tweets:
            results.append(f"🐦 Twitter: {tweet[:200]}")
    except:
        pass

    # 16. Google News
    try:
        url = f"https://news.google.com/rss/search?q={query_encoded}"
        r = requests.get(url, timeout=6)
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:2]:
            title = item.find("title").text if item.find("title") is not None else ""
            results.append(f"📰 Google News: {title[:200]}")
    except:
        pass

    # 17. Product Hunt
    try:
        url = f"https://www.producthunt.com/search?q={query}"
        r = requests.get(url, timeout=6)
        products = re.findall(r'<a[^>]*class="[^"]*product-card[^"]*"[^>]*>(.*?)</a>', r.text)[:2]
        for product in products:
            results.append(f"🚀 Product Hunt: {product[:200]}")
    except:
        pass

    # 18. Dev.to
    try:
        url = f"https://dev.to/search?q={query}"
        r = requests.get(url, timeout=6)
        articles = re.findall(r'<h3[^>]*>(.*?)</h3>', r.text)[:2]
        for article in articles:
            results.append(f"📝 Dev.to: {article[:200]}")
    except:
        pass

    # 19. Hacker News
    try:
        url = "https://news.ycombinator.com/rss"
        r = requests.get(url, timeout=5)
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:5]:
            title = item.find("title").text if item.find("title") is not None else ""
            if query.lower() in title.lower():
                results.append(f"📰 Hacker News: {title[:200]}")
    except:
        pass

    # 20. Unsplash
    try:
        url = f"https://unsplash.com/napi/search/photos?query={query}&per_page=2"
        r = requests.get(url, timeout=6)
        data = r.json()
        for photo in data.get("results", []):
            desc = photo.get('description', 'No description')
            results.append(f"📷 Unsplash: {desc[:200]}")
    except:
        pass

    # 21. Genius (Lyrics)
    try:
        url = f"https://genius.com/search?q={query}"
        r = requests.get(url, timeout=6)
        titles = re.findall(r'<span class="highlighted">(.*?)</span>', r.text)[:2]
        for title in titles:
            results.append(f"🎤 Genius: {title[:200]}")
    except:
        pass

    # 22. Spotify (via search)
    try:
        url = f"https://open.spotify.com/search/{query_encoded}"
        r = requests.get(url, timeout=6)
        songs = re.findall(r'<span class="Type__TypeElement-sc-gj6m8l-0[^"]*"[^>]*>(.*?)</span>', r.text)[:2]
        for song in songs:
            results.append(f"🎵 Spotify: {song[:200]}")
    except:
        pass

    if not results:
        fallback = [
            f"🔍 No results found for '{query}'. Try being more specific or check the spelling.",
            f"🤔 Couldn't find anything for '{query}'. Try a different search term.",
            f"📭 No results for '{query}'. Search for a person, place, or thing."
        ]
        return random.choice(fallback)

    # Remove duplicates
    seen = set()
    unique_results = []
    for result in results:
        if result[:50] not in seen:
            seen.add(result[:50])
            unique_results.append(result)

    return "\n\n".join(unique_results[:8])

def search_wikipedia(query):
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        r = requests.get(url, timeout=8)
        data = r.json()
        if data.get("extract"):
            return f"📚 Wikipedia: {data['extract'][:800]}"
        return f"❌ No Wikipedia page found for '{query}'"
    except:
        return f"❌ Could not reach Wikipedia for '{query}'"

def search_web_full(query):
    return search_web(query)

# ============================================
# 🎬 VIDEO GENERATION (Agnes AI)
# ============================================
def create_video_task(prompt):
    """Create a video generation task and return the video_id."""
    if not AGNES_API_KEY:
        return None
    
    url = "https://apihub.agnes-ai.com/v1/videos"
    headers = {
        "Authorization": f"Bearer {AGNES_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "agnes-video-v2.0",
        "prompt": prompt,
        "width": 1152,
        "height": 768,
        "num_frames": 121,
        "frame_rate": 24,
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        data = response.json()
        return data.get("video_id") or data.get("id")
    except Exception as e:
        logging.error(f"❌ Video creation error: {e}")
        return None

def poll_video_status(video_id):
    """Poll the video status until completed or failed."""
    if not AGNES_API_KEY:
        return {"success": False, "error": "No API key"}
    
    url = "https://apihub.agnes-ai.com/agnesapi"
    params = {"video_id": video_id}
    headers = {"Authorization": f"Bearer {AGNES_API_KEY}"}
    
    for attempt in range(60):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            data = response.json()
            status = str(data.get("status", "")).lower()
            
            if status in {"succeeded", "success", "completed", "done"}:
                video_url = data.get("video_url") or data.get("url")
                return {"success": True, "url": video_url}
            if status in {"failed", "error", "cancelled"}:
                return {"success": False, "error": data}
        except Exception as e:
            logging.error(f"❌ Poll error: {e}")
        
        time.sleep(5)
    
    return {"success": False, "error": "Timed out waiting for video."}

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

🔍 FEATURES:
- Search 20+ sources: DuckDuckGo, Wikipedia, GitHub, Reddit, YouTube, News, and more
- Save and recall notes
- Math solver, unit converter
- Time in cities, countdowns
- Brainstorm, pros/cons, would you rather
- Palindrome check, reverse text
- Image generation and analysis
- Video generation via Agnes AI"""}

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
        "🔍 **Search ANYTHING:**\n"
        "• 'search for Elon Musk'\n"
        "• 'search Wikipedia for AI'\n\n"
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
        if re.search(r'make a video of (.+)', text, re.IGNORECASE):
            match = re.search(r'make a video of (.+)', text, re.IGNORECASE)
            prompt = match.group(1).strip()
            
            if not AGNES_API_KEY:
                await update.message.reply_text("❌ Video API key not configured. Add AGNES_API_KEY to environment.")
                return
            
            if not prompt:
                await update.message.reply_text("⚠️ What kind of video do you want? Example: 'make a video of a cat walking'")
                return
            
            await update.message.reply_text("🎬 Generating your video... this may take 2-5 minutes.")
            
            video_id = create_video_task(prompt)
            if not video_id:
                await update.message.reply_text("❌ Failed to create video task. Try again.")
                return
            
            result = poll_video_status(video_id)
            
            if result["success"] and result.get("url"):
                await update.message.reply_video(result["url"], caption=f"🎥 Here's your video: {prompt}")
            else:
                await update.message.reply_text(f"❌ Video generation failed: {result.get('error', 'Unknown error')}")
            return

        # --- SEARCH ---
        if re.search(r'search for (.+)', text, re.IGNORECASE):
            match = re.search(r'search for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"🔍 Searching 20+ sources for '{query}'...")
            result = search_web(query)
            await update.message.reply_text(result[:4000])
            return

        # --- SEARCH: Wikipedia ---
        if re.search(r'search wikipedia for (.+)', text, re.IGNORECASE):
            match = re.search(r'search wikipedia for (.+)', text, re.IGNORECASE)
            query = match.group(1).strip()
            await update.message.reply_text(f"📚 Searching Wikipedia for '{query}'...")
            result = search_wikipedia(query)
            await update.message.reply_text(result[:1000])
            return

        # --- NOTES: SAVE ---
        if re.search(r'save note(?: |:)(.+)', text, re.IGNORECASE):
            match = re.search(r'save note(?: |:)(.+)', text, re.IGNORECASE)
            content = match.group(1).strip()
            title = content[:30] + "..." if len(content) > 30 else content
            save_note(user_id, title, content)
            await update.message.reply_text(f"✅ Note saved: '{title}'")
            return

        # --- NOTES: RECALL ---
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

        # --- NOTES: SHOW ALL ---
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

        # --- NOTES: DELETE ---
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

        # --- MATH SOLVER ---
        if re.search(r'solve (.+)', text, re.IGNORECASE):
            match = re.search(r'solve (.+)', text, re.IGNORECASE)
            expr = match.group(1).strip()
            try:
                result = eval(expr)
                await update.message.reply_text(f"✅ {expr} = {result}")
            except:
                await update.message.reply_text("❌ Could not solve that. Try: 2 + 2")
            return

        # --- UNIT CONVERTER ---
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
                await update.message.reply_text("❌ Conversion not supported. Try: km to miles, kg to lbs")
            return

        # --- TIME IN CITY ---
        if re.search(r'time in (\w+)', text, re.IGNORECASE):
            match = re.search(r'time in (\w+)', text, re.IGNORECASE)
            city = match.group(1).strip()
            timezones = {
                'tokyo': 9, 'new york': -5, 'london': 0, 'manila': 8,
                'sydney': 11, 'dubai': 4, 'singapore': 8, 'paris': 1,
                'berlin': 1, 'rome': 1, 'los angeles': -8, 'chicago': -6,
                'houston': -6, 'phoenix': -7, 'philadelphia': -5,
                'san antonio': -6, 'san diego': -8, 'dallas': -6,
                'san jose': -8, 'austin': -6, 'jacksonville': -5,
                'fort worth': -6, 'columbus': -5, 'charlotte': -5,
                'san francisco': -8, 'indianapolis': -5, 'seattle': -8,
                'denver': -7, 'washington': -5, 'boston': -5,
                'el paso': -7, 'nashville': -6, 'detroit': -5,
                'oklahoma city': -6, 'portland': -8, 'las vegas': -8,
                'memphis': -6, 'louisville': -5, 'baltimore': -5,
                'milwaukee': -6, 'albuquerque': -7, 'tucson': -7,
                'fresno': -8, 'sacramento': -8, 'miami': -5,
                'atlanta': -5, 'cairo': 2, 'capetown': 2,
                'lagos': 1, 'nairobi': 3, 'addis ababa': 3,
                'casablanca': 1, 'tunis': 1, 'algiers': 1,
                'tripoli': 2, 'khartoum': 2, 'accra': 0,
                'dakar': 0, 'bamako': 0, 'ouagadougou': 0,
                'niamey': 1, "n'djamena": 1, 'bangui': 1,
                'brazzaville': 1, 'kinshasa': 1, 'libreville': 1,
                'malabo': 1, 'yaounde': 1, 'abuja': 1,
                'lome': 0, 'cotonou': 1, 'porto-novo': 1,
                'lilongwe': 2, 'harare': 2, 'gaborone': 2,
                'windhoek': 2, 'luanda': 1, 'maputo': 2,
                'antananarivo': 3, 'port louis': 4, 'victoria': 4,
                'moroni': 3, 'djibouti': 3, 'asmera': 3,
                'mogadishu': 3, 'kampala': 3, 'kigali': 2,
                'bujumbura': 2, 'dar es salaam': 3, 'zanzibar': 3,
                'dodoma': 3, 'lusaka': 2, 'blantyre': 2,
                'mbabane': 2, 'maseru': 2
            }
            city_lower = city.lower()
            if city_lower in timezones:
                offset = timezones[city_lower]
                utc_now = datetime.utcnow()
                local_time = utc_now + timedelta(hours=offset)
                await update.message.reply_text(f"🕐 Time in {city.title()}: {local_time.strftime('%I:%M %p')}")
            else:
                await update.message.reply_text(f"❌ I don't have timezone info for {city}. Try: Tokyo, New York, London, Manila")
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

        # --- PROS AND CONS ---
        if re.search(r'pros and cons of (.+)', text, re.IGNORECASE):
            match = re.search(r'pros and cons of (.+)', text, re.IGNORECASE)
            topic = match.group(1).strip()
            pros = [
                f"✅ Easy to start",
                f"✅ Low cost",
                f"✅ High demand",
                f"✅ Scalable",
                f"✅ Fun to work on",
            ]
            cons = [
                f"❌ Takes time",
                f"❌ Requires learning",
                f"❌ Competition",
                f"❌ Need patience",
                f"❌ Not guaranteed success",
            ]
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

        # --- REVERSE TEXT ---
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
    logging.info("🚀 Petoy 2.0 starting with VIDEO GENERATION...")

    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot.add_handler(MessageHandler(filters.PHOTO, handle_message))

    logging.info("✅ Petoy 2.0 is running with VIDEO GENERATION!")
    bot.run_polling()

if __name__ == "__main__":
    main()