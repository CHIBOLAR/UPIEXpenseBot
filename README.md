# Expense Bot Setup

## Quick Start

1. **Install Python dependencies:**
```bash
pip install -r requirements.txt
```

2. **Test AI parsing first:**
```bash
python test_ai.py
```

3. **Set up your .env file with real API keys**

4. **Run the bot:**
```bash
python bot.py
```

## API Keys Needed

1. **Telegram Bot Token:**
   - Message @BotFather on Telegram
   - `/newbot` → follow instructions
   - Copy token to .env

2. **Gemini API Key:**
   - Go to https://makersuite.google.com/app/apikey
   - Create API key
   - Copy to .env

3. **Google Sheets API:**
   - Google Cloud Console → New Project
   - Enable Google Sheets API
   - Create Service Account
   - Download JSON credentials
   - Extract values for .env

## Bot Commands

- `/start` - Welcome message
- `/test` - Test AI parsing
- `/sheet` - Get your spreadsheet link
- Send text: "Lunch $15 McDonald's"
- Send photo: Receipt screenshot

## Testing

Run `python test_ai.py` first to make sure Gemini API works before starting the full bot.

**Total code: ~200 lines Python (not 1500!)**
