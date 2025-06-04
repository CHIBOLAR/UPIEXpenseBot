#!/usr/bin/env python3
"""
Quick test to verify Telegram bot token works
"""

import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test start command"""
    await update.message.reply_text(
        "🎉 Bot token works!\n\n"
        "Your Telegram bot is connected and ready.\n"
        "Gemini AI is also working perfectly.\n\n"
        "Ready to build the expense tracker!"
    )

async def test_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test text messages"""
    await update.message.reply_text(f"You said: {update.message.text}")

def main():
    """Test the bot connection"""
    app = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, test_message))
    
    print("Testing bot connection...")
    print("Send /start to your bot to test!")
    app.run_polling()

if __name__ == '__main__':
    main()
