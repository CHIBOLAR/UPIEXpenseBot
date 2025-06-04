# Railway Deployment Instructions for Telegram Bot

## Your project is now ready for Railway deployment! 

### Step 1: Commit Changes to Git
```bash
git add .
git commit -m "Prepare for Railway deployment"
git push
```

### Step 2: Deploy on Railway
1. Go to https://railway.app
2. Click "New Project"  
3. Select "Deploy from GitHub repo"
4. Choose your telegram4.0 repository
5. Railway will automatically detect it's a Python project

### Step 3: Configure Environment Variables
Go to your Railway project → Variables tab and add these:

**TELEGRAM_BOT_TOKEN**
- Get from @BotFather on Telegram
- Value: `your_actual_bot_token`

**GEMINI_API_KEY** 
- Get from https://makersuite.google.com/app/apikey
- Value: `your_actual_gemini_key`

**GOOGLE_CREDENTIALS**
- Copy the entire content from RAILWAY_ENV_TEMPLATE.txt
- This includes your Google Service Account JSON

**RAILWAY_ENVIRONMENT**
- Value: `production`

**PORT**
- Value: `8080`

### Step 4: Deploy
- Railway will automatically deploy when you push to GitHub
- Check the deployment logs for any issues
- Your bot should start automatically

### Step 5: Test Your Bot
- Message your bot on Telegram with `/start`
- Test expense tracking features
- Verify Google Sheets integration works

## Files Modified for Railway:
✅ bot.py - Added health check server and Railway detection
✅ requirements.txt - Added gunicorn for production
✅ Procfile - Added for Railway process management  
✅ railway.toml - Railway configuration
✅ runtime.txt - Python version specification
✅ Removed package.json (was for Node.js, not needed)

## Your bot will:
- ✅ Run 24/7 on Railway
- ✅ Handle multiple users simultaneously  
- ✅ Save expenses to Google Sheets
- ✅ Process receipt images with OCR
- ✅ Automatically categorize expenses

## Troubleshooting:
- If deployment fails, check the Railway logs
- Ensure all environment variables are set correctly
- Verify your bot token is valid
- Make sure Google Service Account has proper permissions

Your Telegram expense bot is ready for the cloud! 🚀