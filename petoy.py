import os
import requests
import json
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

# ============================================
# Environment Variables (set in Render)
# ============================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

app = Flask(__name__)

def ask_gemini(question):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": question}]}]}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        if "candidates" in result:
            return result["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return f"Error: {result}"
    except Exception as e:
        return f"Error: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    reply = ask_gemini(user_message)
    await update.message.reply_text(reply)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Hello! I'm Petoy 2.0. Send me anything!")

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update_data = request.get_json()
        update = Update.de_json(update_data, None)
        
        # Build the application
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Process the update
        application.process_update(update)
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return "🤖 Petoy 2.0 is running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
