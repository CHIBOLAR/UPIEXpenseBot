import os, json, logging, re
from datetime import datetime
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from PIL import Image
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Gemini AI
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')

# File paths and global variables
USERS_FILE = 'users.json'
CATEGORIES_FILE = 'categories.json'
user_sheets = {}
user_categories = {}
pending_expenses = {}
user_states = {}

# Conversation states
ADDING_CATEGORY, EDITING_EXPENSE, ADMIN_APPROVAL = range(3)

# Default categories
DEFAULT_CATEGORIES = {
    'food': {'keywords': ['zomato', 'swiggy', 'restaurant', 'food', 'lunch', 'dinner', 'breakfast'], 'emoji': '🍽️'},
    'transport': {'keywords': ['uber', 'ola', 'petrol', 'taxi', 'metro', 'bus', 'train'], 'emoji': '🚗'},
    'shopping': {'keywords': ['amazon', 'flipkart', 'shopping', 'mall', 'clothes'], 'emoji': '🛒'},
    'groceries': {'keywords': ['grocery', 'vegetables', 'milk', 'fruits', 'supermarket'], 'emoji': '🥕'},
    'medical': {'keywords': ['hospital', 'doctor', 'medicine', 'pharmacy'], 'emoji': '💊'},
    'entertainment': {'keywords': ['movie', 'cinema', 'game', 'music', 'netflix'], 'emoji': '🎬'},
    'utilities': {'keywords': ['electricity', 'water', 'gas', 'internet', 'mobile'], 'emoji': '⚡'},
    'miscellaneous': {'keywords': [], 'emoji': '📝'}
}

def load_users():
    """Load user data from JSON file"""
    global user_sheets
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r') as f:
                user_sheets = json.load(f)
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        user_sheets = {}

def save_users():
    """Save user data to JSON file"""
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(user_sheets, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving users: {e}")

def load_categories():
    """Load user categories from JSON file"""
    global user_categories
    try:
        if os.path.exists(CATEGORIES_FILE):
            with open(CATEGORIES_FILE, 'r') as f:
                user_categories = json.load(f)
        else:
            user_categories = {}
    except Exception as e:
        logger.error(f"Error loading categories: {e}")
        user_categories = {}

def save_categories():
    """Save user categories to JSON file"""
    try:
        with open(CATEGORIES_FILE, 'w') as f:
            json.dump(user_categories, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving categories: {e}")

def get_user_categories(user_id):
    """Get categories for a specific user"""
    user_id_str = str(user_id)
    if user_id_str not in user_categories:
        user_categories[user_id_str] = DEFAULT_CATEGORIES.copy()
        save_categories()
    return user_categories[user_id_str]

def extract_with_regex(text, user_id):
    """Extract expense information using regex patterns"""
    text_lower = text.lower()
    
    # Extract amount
    amount = None
    for pattern in [r'₹\s*(\d+(?:\.\d{2})?)', r'rs\.?\s*(\d+(?:\.\d{2})?)', 
                   r'(\d+(?:\.\d{2})?)\s*rupees?', r'paid\s+(\d+(?:\.\d{2})?)']:
        match = re.search(pattern, text_lower)
        if match:
            amount = float(match.group(1))
            break
    
    # Extract payment method
    payment_method = 'cash'
    if any(k in text_lower for k in ['paytm', 'gpay', 'phonepe', 'upi']):
        payment_method = 'upi'
    elif any(k in text_lower for k in ['card', 'credit', 'debit']):
        payment_method = 'card'
    
    # Extract merchant
    merchant = 'Unknown'
    for pattern in [r'(?:at|from|to)\s+([a-zA-Z][a-zA-Z0-9\s&.,-]{2,30})', 
                   r'([a-zA-Z][a-zA-Z0-9\s&.,-]{2,30})\s+(?:₹|rs|paid)']:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().title()
            if len(candidate) > 2:
                merchant = candidate
                break
    
    # Extract category using user's custom categories
    categories = get_user_categories(user_id)
    category = 'miscellaneous'
    for cat, cat_data in categories.items():
        if any(keyword in text_lower for keyword in cat_data['keywords']):
            category = cat
            break
    
    return {
        'amount': amount, 
        'category': category, 
        'payment_method': payment_method,
        'description': text[:50], 
        'merchant': merchant, 
        'date': datetime.now().strftime('%Y-%m-%d')
    }

def get_google_client():
    """Initialize Google Sheets client"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file('telegram_service_account.json', scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Google client error: {e}")
        return None

def get_user_sheet(user_id):
    """Get or create user's Google Sheet"""
    try:
        gc = get_google_client()
        if not gc: 
            return None
            
        if str(user_id) in user_sheets:
            try:
                return gc.open_by_key(user_sheets[str(user_id)]).sheet1
            except: 
                pass
        
        # Create new sheet
        spreadsheet = gc.create(f"Expenses_User_{user_id}")
        sheet = spreadsheet.sheet1
        
        # Add headers
        sheet.append_row(['Date', 'Amount (₹)', 'Category', 'Description', 'Payment Method', 'Merchant', 'Status'])
        
        # Store sheet ID
        user_sheets[str(user_id)] = spreadsheet.id
        save_users()
        
        # Share with user if email is available (optional)
        try:
            spreadsheet.share('', perm_type='anyone', role='writer')
        except:
            pass
            
        return sheet
    except Exception as e:
        logger.error(f"Sheet error: {e}")
        return None

async def parse_expense_ai(text, user_id):
    """Parse expense using AI with user's custom categories"""
    try:
        categories = get_user_categories(user_id)
        category_list = list(categories.keys())
        
        prompt = f"""Parse this expense text: "{text}"
        
Available categories: {', '.join(category_list)}

Return ONLY valid JSON in this exact format:
{{"amount": number, "category": "one_of_the_categories_above", "description": "brief description", "merchant": "merchant name", "payment_method": "upi|cash|card", "date": "{datetime.now().strftime('%Y-%m-%d')}"}}

Choose the most appropriate category from the list above."""
        
        response = model.generate_content(prompt)
        result = response.text.strip()
        
        # Clean up the response
        if '```json' in result: 
            result = result.split('```json')[1].split('```')[0].strip()
        elif '```' in result: 
            result = result.split('```')[1].split('```')[0].strip()
        
        return json.loads(result)
    except Exception as e:
        logger.error(f"AI parsing error: {e}")
        return None

def clean_ocr_text(image_bytes):
    """Extract text from UPI screenshot using OCR"""
    try:
        image = Image.open(BytesIO(image_bytes))
        response = model.generate_content([
            "Extract all payment details from this image including amounts, merchant names, payment method, and any other transaction details. Be very detailed:", 
            image
        ])
        return response.text.strip()
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return None

def add_expense_to_sheet(user_id, expense_data, status="Confirmed"):
    """Add expense to Google Sheet"""
    try:
        sheet = get_user_sheet(user_id)
        if not sheet: 
            return False
            
        row = [
            expense_data['date'], 
            expense_data['amount'], 
            expense_data['category'],
            expense_data['description'], 
            expense_data['payment_method'], 
            expense_data.get('merchant', 'Unknown'),
            status
        ]
        sheet.append_row(row)
        return True
    except Exception as e:
        logger.error(f"Sheet add error: {e}")
        return False

def create_approval_keyboard(expense_id):
    """Create keyboard for expense approval"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{expense_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{expense_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{expense_id}")
        ]
    ])

def create_edit_keyboard(expense_id):
    """Create keyboard for editing expense"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Amount", callback_data=f"edit_amount_{expense_id}"),
            InlineKeyboardButton("📂 Category", callback_data=f"edit_category_{expense_id}")
        ],
        [
            InlineKeyboardButton("🏪 Merchant", callback_data=f"edit_merchant_{expense_id}"),
            InlineKeyboardButton("💳 Payment", callback_data=f"edit_payment_{expense_id}")
        ],
        [
            InlineKeyboardButton("✅ Save", callback_data=f"save_{expense_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{expense_id}")
        ]
    ])

def create_category_keyboard(user_id):
    """Create keyboard for category selection"""
    categories = get_user_categories(user_id)
    keyboard = []
    row = []
    
    for cat, cat_data in categories.items():
        emoji = cat_data.get('emoji', '📝')
        button = InlineKeyboardButton(f"{emoji} {cat.title()}", callback_data=f"cat_{cat}")
        row.append(button)
        
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("➕ Add New Category", callback_data="add_category")])
    return InlineKeyboardMarkup(keyboard)

def create_main_menu_keyboard():
    """Create main menu keyboard"""
    keyboard = [
        [KeyboardButton("📊 View Sheet"), KeyboardButton("📂 Manage Categories")],
        [KeyboardButton("📈 Monthly Summary"), KeyboardButton("❓ Help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and user onboarding"""
    user = update.effective_user
    
    # Create welcome message
    welcome_message = f"""
🎉 **Welcome to Smart Expense Tracker, {user.first_name}!** 

I'm your personal expense assistant that makes tracking money super easy! 

**✨ What I can do:**
• 📸 Read UPI screenshots automatically
• 💬 Parse text expenses ("Lunch ₹350 at McDonald's")
• 📊 Store everything in your personal Google Sheet
• 🔍 Smart categorization with custom categories
• ✏️ Edit and approve expenses before saving
• 📈 Generate expense summaries

**🚀 Getting Started:**
1. Send me a UPI screenshot or type an expense
2. I'll extract all details automatically
3. Review and approve before it's saved
4. Access your data anytime in Google Sheets

**💡 Examples:**
• *"Paid ₹500 for groceries at BigBazaar"*
• *"Uber ride ₹150"*
• *Screenshot of any payment*

Ready to start tracking? Send me your first expense! 💰

*Use the menu below or type /help for more commands.*
"""
    
    await update.message.reply_text(
        welcome_message, 
        reply_markup=create_main_menu_keyboard()
    )
    
    # Initialize user data
    user_id = user.id
    get_user_categories(user_id)  # Initialize with default categories
    get_user_sheet(user_id)  # Create sheet if doesn't exist

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information"""
    help_text = """
🆘 **Help & Commands**

**📝 Adding Expenses:**
• Send UPI screenshot
• Type: "Lunch ₹350 at restaurant"
• Format: "Description ₹amount at merchant"

**🎛️ Commands:**
• /start - Welcome & setup
• /sheet - Get your Google Sheet link
• /categories - Manage expense categories
• /summary - Monthly expense summary
• /help - Show this help

**📂 Category Management:**
• Add custom categories
• Set keywords for auto-detection
• Edit existing categories

**✏️ Editing Expenses:**
• Review before saving
• Edit amount, category, merchant
• Approve or reject expenses

**🔧 Troubleshooting:**
• Unclear screenshot? Try better lighting
• Can't parse text? Use format: "item ₹amount"
• Missing category? Add custom ones

Need more help? Contact support!
"""
    
    await update.message.reply_text(help_text)

async def get_sheet_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send Google Sheet link to user"""
    user_id = update.effective_user.id
    
    if str(user_id) in user_sheets:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{user_sheets[str(user_id)]}"
        message = f"""
📊 **Your Expense Sheet**

🔗 **Link:** {sheet_url}

**📋 Features:**
• Real-time expense tracking
• Automatic categorization
• Payment method tracking
• Monthly summaries
• Export to Excel/PDF

*Bookmark this link for quick access!*
"""
        await update.message.reply_text(message)
    else:
        await update.message.reply_text(
            "📊 No expenses recorded yet. Send me your first expense to create your sheet!"
        )

async def manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show category management interface"""
    user_id = update.effective_user.id
    categories = get_user_categories(user_id)
    
    message = "📂 **Manage Categories**\n\nSelect a category to edit or add a new one:"
    
    await update.message.reply_text(
        message,
        reply_markup=create_category_keyboard(user_id)
    )

async def monthly_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate monthly expense summary"""
    user_id = update.effective_user.id
    
    try:
        sheet = get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Error accessing your expense sheet.")
            return
        
        # Get current month data
        current_month = datetime.now().strftime('%Y-%m')
        all_records = sheet.get_all_records()
        
        # Filter current month expenses
        month_expenses = [r for r in all_records if r['Date'].startswith(current_month)]
        
        if not month_expenses:
            await update.message.reply_text(f"📊 No expenses found for {current_month}")
            return
        
        # Calculate summary
        total_amount = sum(float(r['Amount (₹)']) for r in month_expenses)
        categories = get_user_categories(user_id)
        
        category_totals = {}
        for expense in month_expenses:
            cat = expense['Category']
            category_totals[cat] = category_totals.get(cat, 0) + float(expense['Amount (₹)'])
        
        # Create summary message
        summary = f"""
📈 **Monthly Summary - {datetime.now().strftime('%B %Y')}**

💰 **Total Spent:** ₹{total_amount:,.2f}
📝 **Total Transactions:** {len(month_expenses)}

📊 **Category Breakdown:**
"""
        
        for cat, amount in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
            emoji = categories.get(cat, {}).get('emoji', '📝')
            percentage = (amount / total_amount) * 100
            summary += f"{emoji} {cat.title()}: ₹{amount:,.2f} ({percentage:.1f}%)\n"
        
        summary += f"\n📊 [View Full Sheet](https://docs.google.com/spreadsheets/d/{user_sheets[str(user_id)]})"
        
        await update.message.reply_text(summary)
        
    except Exception as e:
        logger.error(f"Summary error: {e}")
        await update.message.reply_text("❌ Error generating summary.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (expenses)"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Handle menu buttons
    if text == "📊 View Sheet":
        await get_sheet_link(update, context)
        return
    elif text == "📂 Manage Categories":
        await manage_categories(update, context)
        return
    elif text == "📈 Monthly Summary":
        await monthly_summary(update, context)
        return
    elif text == "❓ Help":
        await help_command(update, context)
        return
    
    # Process as expense
    await update.message.reply_text("🔄 Processing your expense...")
    
    # Try regex first
    expense_data = extract_with_regex(text, user_id)
    
    # If regex fails, use AI
    if not expense_data['amount']:
        expense_data = await parse_expense_ai(text, user_id)
    
    if expense_data and expense_data.get('amount'):
        expense_id = f"exp_{user_id}_{int(datetime.now().timestamp())}"
        pending_expenses[expense_id] = expense_data
        
        categories = get_user_categories(user_id)
        category_emoji = categories.get(expense_data['category'], {}).get('emoji', '📝')
        
        response = f"""
💰 **Expense Details:**

**Amount:** ₹{expense_data['amount']}
**Merchant:** {expense_data.get('merchant', 'Unknown')}
**Category:** {category_emoji} {expense_data['category'].title()}
**Payment:** {expense_data['payment_method'].upper()}
**Date:** {expense_data['date']}

Please review and approve:
"""
        
        await update.message.reply_text(
            response, 
            reply_markup=create_approval_keyboard(expense_id)
        )
    else:
        await update.message.reply_text(
            "❌ Couldn't parse your expense. Please try:\n\n"
            "• *'Lunch ₹350 at McDonald's'*\n"
            "• *'Paid ₹500 for groceries'*\n"
            "• *'Uber ride ₹150'*"
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages (UPI screenshots)"""
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("📸 Reading your screenshot...")
        
        # Download image
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        
        # Extract text using OCR
        extracted_text = clean_ocr_text(image_bytes)
        
        if extracted_text:
            await update.message.reply_text(f"📝 Extracted text:\n{extracted_text[:200]}...")
            
            # Parse extracted text
            expense_data = await parse_expense_ai(extracted_text, user_id)
            
            if expense_data and expense_data.get('amount'):
                expense_id = f"exp_{user_id}_{int(datetime.now().timestamp())}"
                pending_expenses[expense_id] = expense_data
                
                categories = get_user_categories(user_id)
                category_emoji = categories.get(expense_data['category'], {}).get('emoji', '📝')
                
                response = f"""
📸 **From Screenshot:**

**Amount:** ₹{expense_data['amount']}
**Merchant:** {expense_data.get('merchant', 'Unknown')}
**Category:** {category_emoji} {expense_data['category'].title()}
**Payment:** {expense_data['payment_method'].upper()}

Please review and approve:
"""
                
                await update.message.reply_text(
                    response, 
                    reply_markup=create_approval_keyboard(expense_id)
                )
            else:
                await update.message.reply_text(
                    f"❌ Couldn't parse expense from image.\n\n"
                    f"**Extracted text:** {extracted_text[:300]}...\n\n"
                    f"Please try typing the expense manually."
                )
        else:
            await update.message.reply_text(
                "❌ Couldn't read the image. Please ensure:\n"
                "• Good lighting\n"
                "• Clear text\n"
                "• UPI transaction screenshot"
            )
            
    except Exception as e:
        logger.error(f"Photo processing error: {e}")
        await update.message.reply_text("❌ Error processing image. Please try again.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Handle approval actions
    if data.startswith(("approve_", "edit_", "reject_")):
        action, expense_id = data.split('_', 1)
        
        if expense_id not in pending_expenses:
            await query.edit_message_text("❌ Expense session expired.")
            return
        
        expense_data = pending_expenses[expense_id]
        
        if action == "approve":
            if add_expense_to_sheet(query.from_user.id, expense_data, "Approved"):
                await query.edit_message_text(
                    f"✅ **Expense Approved & Saved!**\n\n"
                    f"Amount: ₹{expense_data['amount']}\n"
                    f"Merchant: {expense_data.get('merchant', 'Unknown')}\n"
                    f"Category: {expense_data['category'].title()}"
                )
            else:
                await query.edit_message_text("❌ Error saving to sheet.")
            del pending_expenses[expense_id]
            
        elif action == "edit":
            user_states[query.from_user.id] = {'editing': expense_id}
            await query.edit_message_text(
                f"✏️ **Edit Expense:**\n\n"
                f"Amount: ₹{expense_data['amount']}\n"
                f"Merchant: {expense_data.get('merchant', 'Unknown')}\n"
                f"Category: {expense_data['category'].title()}\n"
                f"Payment: {expense_data['payment_method'].upper()}\n\n"
                f"What would you like to edit?",
                reply_markup=create_edit_keyboard(expense_id)
            )
            
        elif action == "reject":
            del pending_expenses[expense_id]
            await query.edit_message_text("❌ Expense rejected and deleted.")
    
    # Handle edit actions
    elif data.startswith("edit_"):
        parts = data.split('_')
        field = parts[1]
        expense_id = '_'.join(parts[2:])
        
        await query.edit_message_text(
            f"Please enter new {field}:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{expense_id}")
            ]])
        )
        
        user_states[query.from_user.id] = {
            'editing': expense_id,
            'field': field
        }
    
    # Handle save/cancel
    elif data.startswith(("save_", "cancel_")):
        action, expense_id = data.split('_', 1)
        
        if action == "save" and expense_id in pending_expenses:
            expense_data = pending_expenses[expense_id]
            if add_expense_to_sheet(query.from_user.id, expense_data, "Edited & Approved"):
                await query.edit_message_text(
                    f"✅ **Expense Saved!**\n\n"
                    f"Amount: ₹{expense_data['amount']}\n"
                    f"Merchant: {expense_data.get('merchant', 'Unknown')}\n"
                    f"Category: {expense_data['category'].title()}"
                )
            else:
                await query.edit_message_text("❌ Error saving to sheet.")
            del pending_expenses[expense_id]
        else:
            if expense_id in pending_expenses:
                del pending_expenses[expense_id]
            await query.edit_message_text("❌ Operation cancelled.")
        
        # Clear user state
        if query.from_user.id in user_states:
            del user_states[query.from_user.id]
    
    # Handle category selection
    elif data.startswith("cat_"):
        category = data[4:]
        # This would be used in category editing context
        await query.edit_message_text(f"Selected category: {category}")

def main():
    """Main function to run the bot"""
    # Fix encoding for Windows console
    import sys
    if sys.platform == "win32":
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    
    # Load data
    load_users()
    load_categories()
    
    # Create application
    app = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("sheet", get_sheet_link))
    app.add_handler(CommandHandler("categories", manage_categories))
    app.add_handler(CommandHandler("summary", monthly_summary))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("🚀 Enhanced Expense Bot started successfully!")
    print("Features: Welcome intro, Google Sheets, Edit/Approval workflow, Custom categories")
    
    app.run_polling()

if __name__ == '__main__':
    main()
