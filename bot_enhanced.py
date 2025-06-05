import os, json, logging, re, traceback, uuid
from datetime import datetime, timedelta
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from PIL import Image
from dotenv import load_dotenv
import asyncio

# Load environment variables
load_dotenv()

# Configure logging with more detailed format
log_filename = f"logs/bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configure Gemini AI
try:
    genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
    model = genai.GenerativeModel('gemini-1.5-flash')
    logger.info("✅ Gemini AI configured successfully")
except Exception as e:
    logger.error(f"❌ Gemini configuration failed: {e}")
    raise

# File paths and global variables
USERS_FILE = 'users.json'
CATEGORIES_FILE = 'categories.json'
user_sheets = {}
user_categories = {}
pending_expenses = {}
user_states = {}
user_conversations = {}

# Conversation states for category management and other flows
ADDING_CATEGORY, EDITING_EXPENSE, ADMIN_APPROVAL, GENERAL_CHAT = range(4)
ADDING_CATEGORY_NAME, ADDING_CATEGORY_EMOJI, ADDING_CATEGORY_KEYWORDS = range(3) # Sub-states for ADDING_CATEGORY
EDITING_AMOUNT, EDITING_CATEGORY = range(2) # Sub-states for EDITING_EXPENSE

# Enhanced Edit Session Management
class EditSession:
    """Manages individual expense editing sessions"""
    def __init__(self, user_id, expense_id, expense_data):
        self.session_id = str(uuid.uuid4())[:8]
        self.user_id = user_id
        self.expense_id = expense_id
        self.expense_data = expense_data.copy()
        self.original_data = expense_data.copy()
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.changes_made = []
        
    def update_field(self, field, new_value, reason="User edit"):
        """Update a field and track the change"""
        old_value = self.expense_data.get(field)
        self.expense_data[field] = new_value
        self.last_activity = datetime.now()
        
        change_record = {
            'field': field,
            'old_value': old_value,
            'new_value': new_value,
            'timestamp': self.last_activity.isoformat(),
            'reason': reason
        }
        self.changes_made.append(change_record)
        
    def is_expired(self, timeout_minutes=30):
        """Check if session has expired"""
        return datetime.now() - self.last_activity > timedelta(minutes=timeout_minutes)
        
    def get_summary(self):
        """Get summary of changes made"""
        if not self.changes_made:
            return "No changes made yet"
        
        summary = "Changes made:\n"
        for change in self.changes_made[-5:]:  # Last 5 changes
            summary += f"• {change['field']}: {change['old_value']} → {change['new_value']}\n"
        return summary

class EditSessionManager:
    """Manages all active edit sessions"""
    def __init__(self):
        self.sessions = {}
        self.user_sessions = {}  # user_id -> session_id mapping
        
    def create_session(self, user_id, expense_id, expense_data):
        """Create new edit session"""
        # Clean up any existing session for this user
        self.cleanup_user_sessions(user_id)
        
        session = EditSession(user_id, expense_id, expense_data)
        self.sessions[session.session_id] = session
        self.user_sessions[user_id] = session.session_id
        
        logger.info(f"✅ Created edit session {session.session_id} for user {user_id}")
        return session
        
    def get_session(self, user_id):
        """Get active session for user"""
        session_id = self.user_sessions.get(user_id)
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            if not session.is_expired():
                return session
            else:
                # Session expired, clean up
                self.cleanup_session(session_id)
        return None
        
    def cleanup_session(self, session_id):
        """Clean up specific session"""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            user_id = session.user_id
            
            del self.sessions[session_id]
            if user_id in self.user_sessions and self.user_sessions[user_id] == session_id:
                del self.user_sessions[user_id]
                
            logger.info(f"🧹 Cleaned up session {session_id} for user {user_id}")
            
    def cleanup_user_sessions(self, user_id):
        """Clean up all sessions for a user"""
        session_id = self.user_sessions.get(user_id)
        if session_id:
            self.cleanup_session(session_id)
            
    def cleanup_expired_sessions(self):
        """Clean up all expired sessions"""
        expired_sessions = []
        for session_id, session in self.sessions.items():
            if session.is_expired():
                expired_sessions.append(session_id)
                
        for session_id in expired_sessions:
            self.cleanup_session(session_id)
            
        if expired_sessions:
            logger.info(f"🧹 Cleaned up {len(expired_sessions)} expired sessions")
            
    def get_session_stats(self):
        """Get statistics about active sessions"""
        total_sessions = len(self.sessions)
        active_users = len(self.user_sessions)
        
        return {
            'total_sessions': total_sessions,
            'active_users': active_users,
            'sessions_by_age': {
                'under_5min': len([s for s in self.sessions.values() if (datetime.now() - s.created_at).seconds < 300]),
                'under_30min': len([s for s in self.sessions.values() if (datetime.now() - s.created_at).seconds < 1800]),
                'over_30min': len([s for s in self.sessions.values() if (datetime.now() - s.created_at).seconds >= 1800])
            }
        }

# Global edit session manager
edit_session_manager = EditSessionManager()

# Enhanced default categories
DEFAULT_CATEGORIES = {
    'food': {
        'keywords': ['zomato', 'swiggy', 'restaurant', 'food', 'lunch', 'dinner', 'breakfast', 'cafe', 'pizza', 'burger', 'meal', 'dining', 'eat', 'kitchen'],
        'emoji': '🍽️'
    },
    'transport': {
        'keywords': ['uber', 'ola', 'petrol', 'taxi', 'metro', 'bus', 'train', 'auto', 'rickshaw', 'fuel', 'parking', 'toll', 'travel', 'commute'],
        'emoji': '🚗'
    },
    'shopping': {
        'keywords': ['amazon', 'flipkart', 'shopping', 'mall', 'clothes', 'shoes', 'electronics', 'mobile', 'laptop', 'gadget', 'purchase'],
        'emoji': '🛒'
    },
    'groceries': {
        'keywords': ['grocery', 'vegetables', 'milk', 'fruits', 'supermarket', 'reliance', 'dmart', 'big bazaar', 'fresh', 'organic'],
        'emoji': '🥕'
    },
    'medical': {
        'keywords': ['hospital', 'doctor', 'medicine', 'pharmacy', 'medical', 'health', 'clinic', 'checkup', 'treatment', 'tablets'],
        'emoji': '💊'
    },
    'entertainment': {
        'keywords': ['movie', 'cinema', 'game', 'music', 'netflix', 'amazon prime', 'hotstar', 'spotify', 'concert', 'show'],
        'emoji': '🎬'
    },
    'utilities': {
        'keywords': ['electricity', 'water', 'gas', 'internet', 'mobile', 'wifi', 'broadband', 'recharge', 'bill', 'phone'],
        'emoji': '⚡'
    },
    'education': {
        'keywords': ['course', 'book', 'education', 'training', 'certification', 'udemy', 'coursera', 'study', 'learning'],
        'emoji': '📚'
    },
    'miscellaneous': {
        'keywords': [],
        'emoji': '📝'
    }
}

class GeminiDecisionEngine:
    """AI-powered decision engine for all bot interactions"""
    
    @staticmethod
    async def analyze_user_intent(message_text, user_context=None):
        """Let Gemini decide what the user wants to do"""
        try:
            context_info = ""
            if user_context:
                context_info = f"User context: {json.dumps(user_context, indent=2)}"
            
            prompt = f"""
You are an intelligent expense tracking bot assistant. Analyze this user message and determine their intent.

User message: "{message_text}"
{context_info}

Respond with ONLY a JSON object in this format:
{{
    "intent": "expense|question|help|sheet_request|category_management|summary|greeting|complaint|unclear",
    "confidence": 0.95,
    "reasoning": "brief explanation", 
    "suggested_action": "what the bot should do",
    "requires_gemini_response": true/false,
    "expense_detected": true/false
}}

Intent definitions:
- expense: User is trying to record an expense
- question: User asking about features/how to use
- help: User needs assistance
- sheet_request: User wants their Google Sheet
- category_management: User wants to manage categories
- summary: User wants expense summary
- greeting: User is greeting/being social
- complaint: User is unhappy with bot performance
- unclear: Message is ambiguous
"""
            
            response = model.generate_content(prompt)
            result = response.text.strip()
            
            # Clean up response
            if '```json' in result:
                result = result.split('```json')[1].split('```')[0].strip()
            elif '```' in result:
                result = result.split('```')[1].split('```')[0].strip()
            
            return json.loads(result)
        except Exception as e:
            logger.error(f"Intent analysis error: {e}")
            return {
                "intent": "unclear",
                "confidence": 0.0,
                "reasoning": "Error in analysis",
                "suggested_action": "Use fallback handling",
                "requires_gemini_response": True,
                "expense_detected": False
            }
    
    @staticmethod
    async def generate_smart_response(message_text, intent_data, user_context=None):
        """Generate contextual response using Gemini"""
        try:
            context_info = ""
            if user_context:
                context_info = f"User context: {json.dumps(user_context, indent=2)}"
            
            prompt = f"""
You are a helpful expense tracking bot. The user sent: "{message_text}"

Intent analysis: {json.dumps(intent_data, indent=2)}
{context_info}

Generate a helpful, friendly response that:
1. Addresses their specific need
2. Provides actionable guidance
3. Keeps them engaged with the bot
4. Is concise but informative
5. Uses emojis appropriately

If they're expressing frustration, be empathetic and helpful.
If they're asking questions, provide clear explanations.
If they're greeting, be warm and welcoming.

Response should be 2-4 sentences maximum unless more detail is needed.
"""
            
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Response generation error: {e}")
            return "I'm having trouble understanding. Could you please rephrase or try again? 🤔"

    @staticmethod
    async def parse_expense_with_ai(text, user_id, categories):
        """Enhanced expense parsing with better AI understanding"""
        try:
            category_list = list(categories.keys())
            
            prompt = f"""
Parse this expense text: "{text}"

Available categories: {', '.join(category_list)}

Extract expense information and return ONLY valid JSON:
{{
    "amount": number (extract from ₹, rs, rupees, paid, etc.),
    "category": "one_from_categories_above",
    "description": "brief description",
    "merchant": "merchant/vendor name",
    "payment_method": "upi|cash|card|online",
    "date": "{datetime.now().strftime('%Y-%m-%d')}",
    "confidence": 0.95,
    "extraction_notes": "what was extracted and why"
}}

Rules:
- Choose the most appropriate category from the list
- If amount not found, set to null
- Merchant should be specific place/vendor name
- Payment method based on keywords (paytm, gpay = upi; card, credit = card; otherwise cash)
- Be smart about inferring context
"""
            
            response = model.generate_content(prompt)
            result = response.text.strip()
            
            # Clean response
            if '```json' in result:
                result = result.split('```json')[1].split('```')[0].strip()
            elif '```' in result:
                result = result.split('```')[1].split('```')[0].strip()
            
            parsed_data = json.loads(result)
            
            # Validate required fields
            if not parsed_data.get('amount') or parsed_data['amount'] <= 0:
                return None
                
            return parsed_data
        except Exception as e:
            logger.error(f"AI expense parsing error: {e}")
            return None

    @staticmethod
    async def process_image_with_ai(image_bytes):
        """Process UPI screenshots with enhanced AI understanding"""
        try:
            image = Image.open(BytesIO(image_bytes))
            
            prompt = """
Analyze this payment/transaction image and extract ALL payment details.

Look for:
- Transaction amount (₹, Rs, rupees)
- Merchant/recipient name
- Payment method (UPI, card, wallet)
- Transaction ID
- Date/time
- Any other transaction details

Provide detailed extraction of all visible text and numbers.
Be very thorough and include everything you can see.
"""
            
            response = model.generate_content([prompt, image])
            extracted_text = response.text.strip()
            
            # Now parse the extracted text for structured data
            parse_prompt = f"""
From this extracted text: "{extracted_text}"

Create a structured expense record as JSON:
{{
    "amount": number,
    "merchant": "merchant name",
    "payment_method": "upi|card|wallet",
    "transaction_id": "if available",
    "raw_text": "original extracted text",
    "confidence": 0.95
}}

Extract the most relevant transaction information.
"""
            
            parse_response = model.generate_content(parse_prompt)
            result = parse_response.text.strip()
            
            if '```json' in result:
                result = result.split('```json')[1].split('```')[0].strip()
            elif '```' in result:
                result = result.split('```')[1].split('```')[0].strip()
            
            return {
                'extracted_text': extracted_text,
                'parsed_data': json.loads(result)
            }
        except Exception as e:
            logger.error(f"Image processing error: {e}")
            return None

# Utility functions with enhanced error handling
def load_users():
    """Load user data with error recovery"""
    global user_sheets
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                user_sheets = json.load(f)
                logger.info(f"✅ Loaded {len(user_sheets)} users")
        else:
            user_sheets = {}
            logger.info("📝 Created new users file")
    except Exception as e:
        logger.error(f"❌ Error loading users: {e}")
        user_sheets = {}

def save_users():
    """Save user data with backup"""
    try:
        # Create backup
        if os.path.exists(USERS_FILE):
            backup_file = f"{USERS_FILE}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(USERS_FILE, backup_file)
        
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_sheets, f, indent=2, ensure_ascii=False)
        logger.info("✅ Users saved successfully")
    except Exception as e:
        logger.error(f"❌ Error saving users: {e}")

def load_categories():
    """Load categories with error recovery"""
    global user_categories
    try:
        if os.path.exists(CATEGORIES_FILE):
            with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
                user_categories = json.load(f)
                logger.info(f"✅ Loaded categories for {len(user_categories)} users")
        else:
            user_categories = {}
            logger.info("📝 Created new categories file")
    except Exception as e:
        logger.error(f"❌ Error loading categories: {e}")
        user_categories = {}

def save_categories():
    """Save categories with backup"""
    try:
        if os.path.exists(CATEGORIES_FILE):
            backup_file = f"{CATEGORIES_FILE}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(CATEGORIES_FILE, backup_file)
        
        with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_categories, f, indent=2, ensure_ascii=False)
        logger.info("✅ Categories saved successfully")
    except Exception as e:
        logger.error(f"❌ Error saving categories: {e}")

def get_user_categories(user_id):
    """Get user categories with initialization"""
    user_id_str = str(user_id)
    if user_id_str not in user_categories:
        user_categories[user_id_str] = DEFAULT_CATEGORIES.copy()
        save_categories()
        logger.info(f"✅ Initialized categories for user {user_id}")
    return user_categories[user_id_str]

def get_google_client():
    """Initialize Google Sheets client with retry logic, supporting environment variable credentials."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            
            # Check for credentials in environment variable first
            service_account_json = os.getenv('GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON')
            if service_account_json:
                try:
                    # Handle potential base64 encoding
                    if not service_account_json.strip().startswith('{'):
                        # Assume it's base64 encoded
                        import base64
                        service_account_json = base64.b64decode(service_account_json).decode('utf-8')
                        logger.info("🔓 Decoded base64 credentials")
                    
                    # Parse JSON directly from environment variable
                    creds_dict = json.loads(service_account_json)
                    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
                    logger.info("✅ Google Sheets client initialized from environment variable")
                except json.JSONDecodeError as je:
                    logger.error(f"❌ JSON parsing error: {je}")
                    logger.error(f"📝 Credential string preview: {service_account_json[:100]}...")
                    raise
                except Exception as pe:
                    logger.error(f"❌ Credential processing error: {pe}")
                    raise
            else:
                # Fallback to local file for local development if env var not set
                # IMPORTANT: Ensure this file is NOT committed to GitHub!
                local_service_account_path = 'C:\\telegram4.0\\telegram_service_account.json'
                if os.path.exists(local_service_account_path):
                    creds = Credentials.from_service_account_file(local_service_account_path, scopes=scope)
                    logger.info("✅ Google Sheets client initialized from local file")
                else:
                    raise FileNotFoundError(f"Google Service Account JSON not found at {local_service_account_path} and GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON env var is not set.")
            
            client = gspread.authorize(creds)
            return client
        except Exception as e:
            logger.error(f"❌ Google client error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
    return None

# Enhanced Google Sheets access with proper user permissions
def get_user_sheet(user_id):
    """Get or create Google Sheet for user with proper permissions"""
    try:
        user_id_str = str(user_id)
        
        # Try to open existing sheet
        if user_id_str in user_sheets:
            try:
                gc = get_google_client()
                sheet = gc.open_by_key(user_sheets[user_id_str]).sheet1
                logger.info(f"✅ Opened existing sheet for user {user_id}")
                return sheet
            except Exception as e:
                logger.warning(f"⚠️ Could not open existing sheet, creating new one: {e}")
        
        # Create new sheet
        gc = get_google_client()
        sheet_name = f"ExpenseTracker_{user_id}_{datetime.now().strftime('%Y%m%d')}"
        spreadsheet = gc.create(sheet_name)
        sheet = spreadsheet.sheet1
        
        # Set up headers
        headers = ['Date', 'Amount', 'Merchant', 'Category', 'Description', 'Payment Method', 'Status', 'Confidence', 'AI Notes']
        sheet.append_row(headers)
        
        # Format headers
        sheet.format('A1:I1', {
            'backgroundColor': {'red': 0.2, 'green': 0.6, 'blue': 0.9},
            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}
        })
        
        # Store sheet ID
        user_sheets[user_id_str] = spreadsheet.id
        save_users()
        
        # FIXED: Grant proper permissions
        try:
            # Option 1: Make publicly editable (easiest for users)
            spreadsheet.share('', perm_type='anyone', role='writer')
            logger.info(f"✅ Sheet made publicly editable for user {user_id}")
            
        except Exception as e:
            logger.warning(f"⚠️ Could not set public permissions, trying alternative: {e}")
            try:
                # Option 2: Share with specific email if provided
                user_email = get_user_email(user_id)  # You'll need to collect this
                if user_email:
                    spreadsheet.share(user_email, perm_type='user', role='writer')
                    logger.info(f"✅ Sheet shared with user email: {user_email}")
                else:
                    # Option 3: Make viewable and provide download link
                    spreadsheet.share('', perm_type='anyone', role='reader')
                    logger.info(f"✅ Sheet made publicly viewable for user {user_id}")
            except Exception as e2:
                logger.error(f"❌ All sharing methods failed: {e2}")
        
        logger.info(f"✅ Created new sheet for user {user_id}: {spreadsheet.id}")
        return sheet
        
    except Exception as e:
        logger.error(f"❌ Sheet error for user {user_id}: {e}")
        return None

def get_sheet_url(user_id):
    """Get the accessible URL for user's sheet"""
    try:
        user_id_str = str(user_id)
        if user_id_str in user_sheets:
            sheet_id = user_sheets[user_id_str]
            # Return editable URL if permissions allow, otherwise view-only
            return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        return None
    except Exception as e:
        logger.error(f"❌ Error getting sheet URL: {e}")
        return None

def get_user_email(user_id):
    """Get user email if stored (you'll need to implement collection)"""
    # This would come from user registration or Telegram profile if available
    # For now, return None - you can enhance this later
    return None

def add_expense_to_sheet(user_id, expense_data, status="Confirmed"):
    """Add expense with enhanced data and error handling"""
    try:
        sheet = get_user_sheet(user_id)
        if not sheet:
            logger.error(f"❌ No sheet available for user {user_id}")
            return False
        
        # Ensure 'description' and 'merchant' keys exist to avoid KeyError
        description = expense_data.get('description', 'No description')
        merchant = expense_data.get('merchant', 'Unknown')
        
        # Enhanced row data
        row = [
            expense_data.get('date', datetime.now().strftime('%Y-%m-%d')),
            expense_data.get('amount', 0),
            merchant, # Merchant column
            expense_data.get('category', 'miscellaneous'),
            description, # Description column
            expense_data.get('payment_method', 'unknown'),
            status,
            expense_data.get('confidence', 'N/A'), # Confidence column
            expense_data.get('extraction_notes', 'Auto-detected') # AI Notes column
        ]
        
        sheet.append_row(row)
        logger.info(f"✅ Added expense to sheet for user {user_id}: ₹{expense_data.get('amount')}")
        return True
    except Exception as e:
        logger.error(f"❌ Error adding expense for user {user_id}: {e}")
        return False

# Keyboard creation functions
def create_approval_keyboard(expense_id):
    """Create expense approval keyboard"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{expense_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{expense_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{expense_id}")
        ]
    ])

# REMOVED: This function is no longer used for the simplified edit flow
# def create_edit_keyboard(expense_id):
#     """Create expense editing keyboard"""
#     return InlineKeyboardMarkup([
#         [
#             InlineKeyboardButton("💰 Amount", callback_data=f"edit_field_amount_{expense_id}"),
#             InlineKeyboardButton("📂 Category", callback_data=f"edit_field_category_{expense_id}")
#         ],
#         [
#             InlineKeyboardButton("🏪 Merchant", callback_data=f"edit_field_merchant_{expense_id}"),
#             InlineKeyboardButton("💳 Payment", callback_data=f"edit_field_payment_method_{expense_id}")
#         ],
#         [
#             InlineKeyboardButton("✅ Save", callback_data=f"save_expense_{expense_id}"),
#             InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_edit_{expense_id}")
#         ]
#     ])

def create_main_menu_keyboard():
    """Create main menu keyboard"""
    keyboard = [
        [KeyboardButton("📊 View Sheet"), KeyboardButton("📂 Categories")],
        [KeyboardButton("📈 Summary"), KeyboardButton("❓ Help")],
        [KeyboardButton("🤖 Chat with AI")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def handle_add_category_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input during the add category flow."""
    user_id = update.effective_user.id
    message_text = update.message.text
    user_state = user_states.get(user_id)

    if not user_state or user_state.get('state') != ADDING_CATEGORY:
        await update.message.reply_text("🤔 No active category creation session. Please use '📂 Categories' → '➕ Add New Category'.")
        return

    current_step = user_state.get('step')
    
    try:
        if current_step == ADDING_CATEGORY_NAME:
            # Validate category name
            category_name = message_text.strip().lower()
            if not category_name or len(category_name) > 50:
                await update.message.reply_text(
                    "❌ Category name should be 1-50 characters. Please send a valid name:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_category")]])
                )
                return
            
            categories = get_user_categories(user_id)
            if category_name in categories:
                await update.message.reply_text(
                    f"❌ Category '{category_name.title()}' already exists! Please choose a different name:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_category")]])
                )
                return
            
            # Store category name and move to emoji step
            user_states[user_id]['category_name'] = category_name
            user_states[user_id]['step'] = ADDING_CATEGORY_EMOJI
            
            await update.message.reply_text(
                f"✅ Category name: '{category_name.title()}'\n\n"
                "Now send me an emoji for this category (e.g., 🍕, 🚗, 💊):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_category")]])
            )
            
        elif current_step == ADDING_CATEGORY_EMOJI:
            # Validate emoji (simple check)
            emoji = message_text.strip()
            if len(emoji) > 5 or not emoji:  # Basic validation
                emoji = "📝"  # Default emoji if invalid
            
            user_states[user_id]['category_emoji'] = emoji
            user_states[user_id]['step'] = ADDING_CATEGORY_KEYWORDS
            
            await update.message.reply_text(
                f"✅ Emoji: {emoji}\n\n"
                "Finally, send me some keywords for automatic detection (comma-separated).\n"
                "Example: 'pizza, restaurant, dominos, food delivery'\n"
                "Or send 'none' to skip:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_category")]])
            )
            
        elif current_step == ADDING_CATEGORY_KEYWORDS:
            # Process keywords
            keywords = []
            if message_text.strip().lower() != 'none':
                keywords = [k.strip().lower() for k in message_text.split(',') if k.strip()]
            
            # Create the category
            category_name = user_states[user_id]['category_name']
            category_emoji = user_states[user_id]['category_emoji']
            
            categories = get_user_categories(user_id)
            categories[category_name] = {
                'emoji': category_emoji,
                'keywords': keywords
            }
            save_categories()
            
            # Clean up state
            del user_states[user_id]
            
            await update.message.reply_text(
                f"🎉 **Category Created Successfully!**\n\n"
                f"{category_emoji} **{category_name.title()}**\n"
                f"Keywords: {', '.join(keywords) if keywords else 'None'}\n\n"
                "Your new category is ready to use!"
            )
            
    except Exception as e:
        logger.error(f"❌ handle_add_category_input error: {e}")
        await update.message.reply_text(
            "❌ Error processing category input. Please try again or cancel.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_category")]])
        )

# Enhanced message handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced welcome with immediate screenshot demo"""
    try:
        user = update.effective_user
        user_id = user.id
        
        # Initialize user data
        get_user_categories(user_id)
        get_user_sheet(user_id)
        
        # Send initial welcome
        await update.message.reply_text(
            f"🎉 **Welcome {user.first_name}!**\n\n"
            f"I'm your AI-powered expense tracking assistant! Let me show you how easy it is to track expenses...",
            reply_markup=create_main_menu_keyboard()
        )
        
        # Send screenshot demo
        demo_message = """
📸 **Try This Right Now:**

**Option 1 - Upload a Screenshot:**
• Take a screenshot of any UPI payment 
• Send it to me - I'll extract all details automatically!

**Option 2 - Type an Expense:**
• Just type: "Lunch ₹350 at McDonald's"
• Or: "Uber ride ₹150"
• Or: "Grocery ₹2500"

**What I'll do:**
✅ Extract amount, merchant, category
✅ Save to your personal Google Sheet
✅ Smart categorization with AI
✅ Interactive editing if needed

**Ready to try?** Send me any expense! 🚀
"""
        
        await update.message.reply_text(demo_message)
        
        # Send quick demo of sheet access
        if str(user_id) in user_sheets:
            sheet_url = get_sheet_url(user_id)
            if sheet_url:
                await update.message.reply_text(
                    f"📊 **Your Google Sheet is ready!**\n\n"
                    f"🔗 [Open My Sheet]({sheet_url})\n\n"
                    f"All your expenses will be automatically saved here with full edit access!"
                )
        else:
            await update.message.reply_text(
                "💡 **First expense?** Your personal Google Sheet will be created automatically when you send your first expense!"
            )
        
        logger.info(f"✅ Enhanced welcome sent to: {user_id} ({user.first_name})")
        
    except Exception as e:
        logger.error(f"❌ Start command error: {e}")
        await update.message.reply_text(
            "🎉 Welcome! I'm your AI expense tracking assistant.\n\n"
            "📸 Upload a payment screenshot or type an expense like 'Lunch ₹350'!\n\n"
            "I'll automatically save everything to your Google Sheet! 💰",
            reply_markup=create_main_menu_keyboard()
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced text handler using Gemini for all decisions"""
    try:
        user_id = update.effective_user.id
        message_text = update.message.text
        
        # Handle menu buttons first
        if message_text in ["📊 View Sheet", "📂 Categories", "📈 Summary", "❓ Help"]:
            await handle_menu_button(update, context, message_text)
            return
        
        # Let Gemini analyze the intent
        await update.message.reply_text("🤖 Analyzing your message...")
        
        user_context = {
            "user_id": user_id,
            "has_sheet": str(user_id) in user_sheets,
            "categories": list(get_user_categories(user_id).keys())
        }
        
        intent_data = await GeminiDecisionEngine.analyze_user_intent(message_text, user_context)
        logger.info(f"Intent analysis for user {user_id}: {intent_data}")
        
        # Handle based on intent
        if intent_data["intent"] == "expense" and intent_data["expense_detected"]:
            await handle_expense_text(update, context, message_text, intent_data)
        elif intent_data["intent"] in ["question", "help", "greeting", "complaint"]:
            ai_response = await GeminiDecisionEngine.generate_smart_response(
                message_text, intent_data, user_context
            )
            await update.message.reply_text(ai_response)
        elif intent_data["intent"] == "sheet_request":
            await get_sheet_link(update, context)
        elif intent_data["intent"] == "summary":
            await monthly_summary(update, context)
        elif intent_data["intent"] == "category_management":
            await manage_categories(update, context)
        else:
            # Unclear intent - let AI handle it conversationally
            ai_response = await GeminiDecisionEngine.generate_smart_response(
                message_text, intent_data, user_context
            )
            await update.message.reply_text(
                ai_response + "\n\n💡 Tip: Send me an expense like 'Lunch ₹350' or upload a payment screenshot!"
            )
            
    except Exception as e:
        logger.error(f"❌ Text handling error: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(
            "😅 I encountered an issue. Let me try again! Could you please resend your message?"
        )

async def handle_expense_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, intent_data: dict):
    """Handle expense text with AI parsing"""
    try:
        user_id = update.effective_user.id
        categories = get_user_categories(user_id)
        
        # Parse expense using AI
        expense_data = await GeminiDecisionEngine.parse_expense_with_ai(text, user_id, categories)
        
        if expense_data and expense_data.get('amount'):
            expense_id = f"exp_{user_id}_{int(datetime.now().timestamp())}"
            pending_expenses[expense_id] = expense_data
            
            category_emoji = categories.get(expense_data['category'], {}).get('emoji', '📝')
            
            response = f"""
💰 **Expense Detected**

**Amount:** ₹{expense_data['amount']}
**Category:** {category_emoji} {expense_data['category'].title()}
**Merchant:** {expense_data.get('merchant', 'Unknown')}
**Payment:** {expense_data.get('payment_method', 'unknown').upper()}
**Date:** {expense_data.get('date')}

**AI Confidence:** {expense_data.get('confidence', 'N/A')}
**Notes:** {expense_data.get('extraction_notes', 'Auto-detected')}

Please review and approve:
"""
            
            await update.message.reply_text(
                response,
                reply_markup=create_approval_keyboard(expense_id)
            )
        else:
            # AI couldn't parse - provide helpful response
            help_response = await GeminiDecisionEngine.generate_smart_response(
                f"Couldn't parse expense from: {text}",
                {"intent": "expense", "expense_detected": False},
                {"suggestion": "provide examples"}
            )
            await update.message.reply_text(help_response)
            
    except Exception as e:
        logger.error(f"❌ Expense text handling error: {e}")
        await update.message.reply_text(
            "🤔 I had trouble understanding that expense. Try: 'Lunch ₹350 at McDonald's'"
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced photo handling with AI processing"""
    try:
        user_id = update.effective_user.id
        
        await update.message.reply_text("📸 Processing your screenshot with AI...")
        
        # Download image
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        
        # Process with AI
        image_result = await GeminiDecisionEngine.process_image_with_ai(image_bytes)
        
        if image_result and image_result.get('parsed_data'):
            # REMOVED: Display of raw extracted text
            # await update.message.reply_text(
            #     f"🔍 **Extracted Text:**\n{image_result['extracted_text'][:300]}{'...' if len(image_result['extracted_text']) > 300 else ''}"
            # )
            
            parsed_data = image_result['parsed_data']
            
            # Try to create expense from parsed data
            if parsed_data.get('amount'):
                categories = get_user_categories(user_id)
                
                # Enhanced expense data
                expense_data = {
                    'amount': parsed_data['amount'],
                    'merchant': parsed_data.get('merchant', 'From Screenshot'),
                    'payment_method': parsed_data.get('payment_method', 'upi'),
                    'description': f"Payment screenshot - {parsed_data.get('merchant', 'Unknown')}",
                    'category': 'miscellaneous',  # Let user confirm category
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'raw_text': image_result['extracted_text'],
                    'confidence': parsed_data.get('confidence', 0.8)
                }
                
                expense_id = f"exp_{user_id}_{int(datetime.now().timestamp())}"
                pending_expenses[expense_id] = expense_data
                
                response = f"""
📱 **From Screenshot:**

**Amount:** ₹{expense_data['amount']}
**Merchant:** {expense_data['merchant']}
**Payment:** {expense_data['payment_method'].upper()}
**Transaction ID:** {parsed_data.get('transaction_id', 'Not found')}

Please review and approve:
"""
                
                await update.message.reply_text(
                    response,
                    reply_markup=create_approval_keyboard(expense_id)
                )
            else:
                await update.message.reply_text(
                    f"🤔 I could read the image but couldn't find amount details:\n\n"
                    f"**Extracted:** {image_result['extracted_text'][:200]}...\n\n"
                    f"Could you please type the expense manually?"
                )
        else:
            await update.message.reply_text(
                "📸 I couldn't clearly read this image. Please ensure:\n"
                "• Good lighting and clear text\n"
                "• Full transaction details visible\n"
                "• Try a different angle if needed"
            )
            
    except Exception as e:
        logger.error(f"❌ Photo processing error: {e}\n{traceback.format_exc()}")
        await update.message.reply_text(
            "📸 Error processing image. Please try again or type the expense manually."
        )

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE, button_text: str):
    """Handle menu button presses"""
    try:
        if button_text == "📊 View Sheet":
            await get_sheet_link(update, context)
        elif button_text == "📂 Categories":
            await manage_categories(update, context)
        elif button_text == "📈 Summary":
            await monthly_summary(update, context)
        elif button_text == "❓ Help":
            await help_command(update, context)
    except Exception as e:
        logger.error(f"❌ Menu button error: {e}")
        await update.message.reply_text("Sorry, there was an error. Please try again.")

async def get_sheet_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send Google Sheet link"""
    try:
        user_id = update.effective_user.id
        
        if str(user_id) in user_sheets:
            sheet_url = f"https://docs.google.com/spreadsheets/d/{user_sheets[str(user_id)]}"
            
            # AI-generated sheet message
            sheet_response = await GeminiDecisionEngine.generate_smart_response(
                "User wants their Google Sheet link",
                {"intent": "sheet_request"},
                {"sheet_url": sheet_url}
            )
            
            message = f"{sheet_response}\n\n🔗 **Your Sheet:** {sheet_url}"
            
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(
                "📊 No expenses recorded yet! Send me your first expense to create your personal Google Sheet."
            )
    except Exception as e:
        logger.error(f"❌ Sheet link error: {e}")
        await update.message.reply_text("❌ Error accessing your sheet. Please try again.")

async def manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced category management with analytics"""
    try:
        user_id = update.effective_user.id
        categories = get_user_categories(user_id)
        
        # Create enhanced category overview
        message = "📂 **Category Management**\n\n"
        message += "**Manage your categories:**"
        
        # Create category buttons
        keyboard = []
        row = []
        
        for cat, cat_data in categories.items():
            emoji = cat_data.get('emoji', '📝')
            button_text = f"{emoji} {cat.title()}"
            button = InlineKeyboardButton(button_text, callback_data=f"cat_detail_{cat}")
            row.append(button)
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        # Management options
        keyboard.extend([
            [InlineKeyboardButton("➕ Add New Category", callback_data="add_category"),
             InlineKeyboardButton("🤖 AI Suggestions", callback_data="ai_categories")],
            [InlineKeyboardButton("📊 Usage Analytics", callback_data="category_analytics")]
        ])
        
        # Determine if it's a new message or an edit to an existing one
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
    except Exception as e:
        logger.error(f"❌ Category management error: {e}")
        await (update.callback_query or update.message).reply_text("❌ Error loading categories. Please try again.")

async def monthly_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI-powered monthly summary with insights"""
    try:
        user_id = update.effective_user.id
        
        sheet = get_user_sheet(user_id)
        if not sheet:
            await update.message.reply_text("❌ Error accessing your expense data.")
            return
        
        # Get current month data
        current_month = datetime.now().strftime('%Y-%m')
        all_records = sheet.get_all_records()
        
        # Filter current month expenses
        month_expenses = [r for r in all_records if str(r.get('Date', '')).startswith(current_month)]
        
        if not month_expenses:
            await update.message.reply_text(f"📊 No expenses found for {datetime.now().strftime('%B %Y')}")
            return
        
        # Calculate summary data
        # Ensure 'Amount' column is used, not 'Amount (₹)' as per the sheet headers
        total_amount = sum(float(r.get('Amount', 0)) for r in month_expenses) 
        categories = get_user_categories(user_id)
        
        category_totals = {}
        payment_method_totals = {}
        
        for expense in month_expenses:
            cat = expense.get('Category', 'miscellaneous')
            payment = expense.get('Payment Method', 'unknown')
            amount = float(expense.get('Amount', 0)) # Ensure 'Amount' column is used
            
            category_totals[cat] = category_totals.get(cat, 0) + amount
            payment_method_totals[payment] = payment_method_totals.get(payment, 0) + amount
        
        # Generate AI insights
        summary_data = {
            "total_amount": total_amount,
            "transaction_count": len(month_expenses),
            "category_breakdown": category_totals,
            "payment_methods": payment_method_totals,
            "month": datetime.now().strftime('%B %Y')
        }
        
        ai_insights = await GeminiDecisionEngine.generate_smart_response(
            "Generate monthly expense insights",
            {"intent": "summary"},
            summary_data
        )
        
        # Create formatted summary
        summary = f"""
📈 **{datetime.now().strftime('%B %Y')} Summary**

💰 **Total Spent:** ₹{total_amount:,.2f}
📝 **Transactions:** {len(month_expenses)}

📊 **Top Categories:**
"""
        
        # Top 5 categories
        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:5]
        for cat, amount in sorted_categories:
            emoji = categories.get(cat, {}).get('emoji', '📝')
            percentage = (amount / total_amount) * 100
            summary += f"{emoji} {cat.title()}: ₹{amount:,.0f} ({percentage:.1f}%)\n"
        
        summary += f"\n🤖 **AI Insights:**\n{ai_insights}"
        
        if str(user_id) in user_sheets:
            sheet_url = f"https://docs.google.com/spreadsheets/d/{user_sheets[str(user_id)]}"
            summary += f"\n\n📊 [View Full Details]({sheet_url})"
        
        await update.message.reply_text(summary)
        
    except Exception as e:
        logger.error(f"❌ Summary error: {e}\n{traceback.format_exc()}")
        await update.message.reply_text("❌ Error generating summary. Please try again.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI-powered help system"""
    try:
        user_id = update.effective_user.id
        user_context = {
            "has_expenses": str(user_id) in user_sheets,
            "category_count": len(get_user_categories(user_id))
        }
        
        help_response = await GeminiDecisionEngine.generate_smart_response(
            "User needs help with the expense tracking bot",
            {"intent": "help"},
            user_context
        )
        
        # Add specific examples
        help_text = f"""{help_response}

📝 **Expense Examples:**
• "Lunch ₹350 at McDonald's"
• "Uber ride ₹150"
• "Grocery shopping ₹2500 at BigBazaar"
• Upload UPI/payment screenshots

🎛️ **Commands:**
• /start - Welcome & setup
• /sheet - Get your Google Sheet
• /help - This help message

💡 **Pro Tips:**
• I understand natural language!
• Screenshots work great for UPI payments
• Ask me anything - I'm powered by AI!
"""
        
        await update.message.reply_text(help_text)
        
    except Exception as e:
        logger.error(f"❌ Help command error: {e}")
        await update.message.reply_text(
            "🆘 **Help**\n\nSend me expenses like 'Lunch ₹350' or upload payment screenshots. "
            "I'll automatically categorize and save them to your Google Sheet!"
        )

# NEW: handle_category_callback implementation
async def handle_category_callback(query, category_data_str: str):
    """
    Handles callbacks for category details.
    The category_data_str will be in format "detail_categoryname".
    """
    try:
        # Parse the category_data_str (e.g., "detail_food")
        parts = category_data_str.split('_', 1)
        if len(parts) < 2:
            await query.edit_message_text(f"❌ Invalid category action: {category_data_str}")
            return

        action = parts[0] # Should be 'detail'
        category_name = parts[1]
        user_id = query.from_user.id
        categories = get_user_categories(user_id)

        if action == "detail":
            if category_name in categories:
                cat_info = categories[category_name]
                message = f"📂 **Category: {cat_info.get('emoji', '')} {category_name.title()}**\n\n"
                message += f"**Emoji:** {cat_info.get('emoji', 'N/A')}\n"
                message += f"**Keywords:** {', '.join(cat_info.get('keywords', ['None']))}\n\n"
                
                # Add back button to category management
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Categories", callback_data="back_to_categories")]
                ])
                await query.edit_message_text(message, reply_markup=keyboard)
            else:
                await query.edit_message_text(f"❌ Category '{category_name.title()}' not found.")
        else:
            await query.edit_message_text(f"Unhandled category action: {action}")

    except Exception as e:
        logger.error(f"❌ handle_category_callback error: {e}\n{traceback.format_exc()}")
        await query.edit_message_text("❌ Error getting category details. Please try again.")

# NEW: handle_add_category implementation
async def handle_add_category(query):
    """Initiates the process of adding a new category."""
    user_id = query.from_user.id
    user_states[user_id] = {'state': ADDING_CATEGORY, 'step': ADDING_CATEGORY_NAME}
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_category")]])
    await query.edit_message_text("Let's add a new category! Please send me the **name** for your new category (e.g., 'Subscriptions').", reply_markup=keyboard)

# NEW: handle_category_analytics implementation
async def handle_category_analytics(query):
    """Fetches and displays usage analytics by category."""
    try:
        user_id = query.from_user.id
        sheet = get_user_sheet(user_id)
        if not sheet:
            await query.edit_message_text("❌ Error accessing your expense data for analytics.")
            return

        all_records = sheet.get_all_records()
        if not all_records:
            await query.edit_message_text("📊 No expenses recorded yet to generate analytics.")
            return

        category_totals = {}
        for expense in all_records:
            cat = expense.get('Category', 'miscellaneous')
            amount = float(expense.get('Amount', 0)) # Assuming 'Amount' is the column name
            category_totals[cat] = category_totals.get(cat, 0) + amount
        
        total_spent = sum(category_totals.values())

        if not category_totals:
            await query.edit_message_text("📊 No categorized expenses found for analytics.")
            return

        message = "📊 **Category Usage Analytics**\n\n"
        message += "Here's how your expenses are distributed across categories:\n\n"
        
        # Sort by amount, descending
        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)

        categories_info = get_user_categories(user_id)

        for cat, amount in sorted_categories:
            emoji = categories_info.get(cat, {}).get('emoji', '📝')
            percentage = (amount / total_spent) * 100 if total_spent > 0 else 0
            message += f"{emoji} **{cat.title()}:** ₹{amount:,.2f} ({percentage:.1f}%)\n"
        
        message += f"\n**Total Expenses Tracked:** ₹{total_spent:,.2f}"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to Categories", callback_data="back_to_categories")]
        ])
        await query.edit_message_text(message, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"❌ handle_category_analytics error: {e}\n{traceback.format_exc()}")
        await query.edit_message_text("❌ Error generating category analytics. Please try again.")

async def handle_robust_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced callback handler with robust edit session management"""
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        # Clean up expired sessions periodically
        edit_session_manager.cleanup_expired_sessions()
        
        # REMOVED: handle_edit_field_callback and set_category/payment callbacks
        # as editing is now chat-based for amount and category.
        # if data.startswith("edit_field_"):
        #     await handle_edit_field_callback(query, data)
        # elif data.startswith("set_category_"):
        #     ...
        # elif data.startswith("set_payment_"):
        #     ...
            
        if data.startswith("confirm_edit_"):
            # This path is likely deprecated by direct save_expense_ logic
            pass 
        elif data.startswith("cancel_edit"): # This handles cancel for both add and edit flows
            # Check for specific cancel_edit_expense_id for edit flow
            if data.startswith("cancel_edit_exp_"):
                expense_id_to_cancel = data.replace("cancel_edit_", "")
                edit_session_manager.cleanup_user_sessions(user_id) # Clean up the edit session
                if expense_id_to_cancel in pending_expenses:
                    del pending_expenses[expense_id_to_cancel] # Remove from pending if it was there
                await query.edit_message_text("❌ Expense editing cancelled.")
            # Check for general cancel for add category flow
            elif data == "cancel_add_category":
                if user_id in user_states and user_states[user_id].get('state') == ADDING_CATEGORY:
                    del user_states[user_id] # Clear ADDING_CATEGORY state
                    await query.edit_message_text("❌ New category creation cancelled.")
                    await manage_categories(update, context) # Go back to category menu
            # NEW: Handle cancel during chat-based expense editing
            elif data.startswith("cancel_edit_conversation_"):
                expense_id_to_cancel = data.replace("cancel_edit_conversation_", "")
                edit_session_manager.cleanup_user_sessions(user_id)
                if user_id in user_states:
                    del user_states[user_id] # Clear user state
                await query.edit_message_text("❌ Expense editing cancelled.")
            else: # Generic cancel, e.g., from AI suggestions
                if user_id in user_states:
                    del user_states[user_id]
                await query.edit_message_text("Okay, cancelled.")
            
        elif data.startswith("save_expense_"):
            await handle_save_expense_callback(query, data)
        
        # Handle approval actions
        elif data.startswith(("approve_", "edit_", "reject_")):
            await handle_expense_callback(query, data)
            
        # Handle category actions (e.g., cat_detail_food)
        elif data.startswith("cat_"):
            category_detail_string = data[4:] # e.g., "detail_food"
            await handle_category_callback(query, category_detail_string)
            
        # Handle special actions
        elif data == "add_category":
            await handle_add_category(query)
            
        elif data == "ai_categories":
            await handle_ai_category_suggestions(query)
            
        # NEW: Handlers for AI category suggestions confirmation/cancel
        elif data.startswith("add_ai_cat_"):
            suggestion_index = int(data.split('_')[-1])
            if user_id in user_states and 'ai_suggestions' in user_states[user_id]:
                suggestions = user_states[user_id]['ai_suggestions']
                if 0 <= suggestion_index < len(suggestions):
                    suggestion = suggestions[suggestion_index]
                    categories = get_user_categories(user_id)
                    
                    # Ensure category name is lowercase for consistency
                    cat_name_lower = suggestion['name'].lower()
                    if cat_name_lower not in categories:
                        categories[cat_name_lower] = {
                            'emoji': suggestion['emoji'], 
                            'keywords': suggestion['keywords']
                        }
                        save_categories()
                        await query.edit_message_text(f"✅ Category '{suggestion['name'].title()}' added!")
                    else:
                        await query.edit_message_text(f"💡 Category '{suggestion['name'].title()}' already exists!")
                    
                    del user_states[user_id]['ai_suggestions'] # Clean up AI suggestions state
                    if user_id in user_states and user_states[user_id].get('state') == ADDING_CATEGORY:
                        del user_states[user_id] # Also clear ADDING_CATEGORY state if active
                else:
                    await query.edit_message_text("Error: Invalid AI suggestion index.")
            else:
                await query.edit_message_text("Error: Could not retrieve AI suggestions. Please try '🤖 AI Suggestions' again.")
        elif data == "cancel_ai_cats":
            if user_id in user_states and 'ai_suggestions' in user_states[user_id]:
                del user_states[user_id]['ai_suggestions']
            await query.edit_message_text("AI category suggestions cancelled.")
            if user_id in user_states and user_states[user_id].get('state') == ADDING_CATEGORY:
                del user_states[user_id] # Also clear ADDING_CATEGORY state if active
            
        # NEW: Handler for Category Analytics
        elif data == "category_analytics":
            await handle_category_analytics(query)

        # NEW: Handler to go back to main category menu
        elif data == "back_to_categories":
            await manage_categories(update, context) # Re-call manage_categories to show the menu

    except Exception as e:
        logger.error(f"❌ Robust callback error: {e}\n{traceback.format_exc()}")
        await query.edit_message_text("❌ Error processing request. Please try again.")

# REMOVED: This function is no longer used for the simplified edit flow
# async def handle_edit_field_callback(query, data):
#     """Handle field editing with session management"""
#     try:
#         user_id = query.from_user.id
#         parts = data.split('_')
#         field_name = parts[2]
#         expense_id = "_".join(parts[3:])
#
#         session = edit_session_manager.get_session(user_id)
#         if not session or session.expense_id != expense_id:
#             await query.edit_message_text(
#                 "❌ Edit session expired or invalid. Please start editing again.",
#                 reply_markup=InlineKeyboardMarkup([[
#                     InlineKeyboardButton("🔄 Start Over", callback_data=f"edit_{expense_id}")
#                 ]])
#             )
#             return
#            
#         session.last_activity = datetime.now()
#        
#         current_value = session.expense_data.get(field_name, "Not set")
#        
#         if field_name == "amount":
#             message = f"💰 **Edit Amount**\n\nCurrent: ₹{current_value}\n\nSend me the new amount (numbers only):"
#             user_states[user_id] = {'editing_field': 'amount', 'session_id': session.session_id}
#            
#         elif field_name == "category":
#             categories = get_user_categories(user_id)
#             message = f"📂 **Edit Category**\n\nCurrent: {current_value}\n\nChoose new category:"
#            
#             keyboard = []
#             row = []
#             for cat, cat_data in categories.items():
#                 emoji = cat_data.get('emoji', '📝')
#                 button = InlineKeyboardButton(f"{emoji} {cat.title()}", callback_data=f"set_category_{cat}")
#                 row.append(button)
#                
#                 if len(row) == 2:
#                     keyboard.append(row)
#                     row = []
#            
#             if row:
#                 keyboard.append(row)
#                
#             keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_edit_{session.expense_id}")])
#            
#             await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
#             return
#            
#         elif field_name == "merchant":
#             message = f"🏪 **Edit Merchant**\n\nCurrent: {current_value}\n\nSend me the new merchant name:"
#             user_states[user_id] = {'editing_field': 'merchant', 'session_id': session.session_id}
#            
#         elif field_name == "payment_method":
#             message = f"💳 **Edit Payment Method**\n\nCurrent: {current_value}\n\nChoose payment method:"
#            
#             keyboard = [
#                 [InlineKeyboardButton("📱 UPI", callback_data="set_payment_upi"),
#                  InlineKeyboardButton("💳 Card", callback_data="set_payment_card")],
#                 [InlineKeyboardButton("💵 Cash", callback_data="set_payment_cash"),
#                  InlineKeyboardButton("🌐 Online", callback_data="set_payment_online")],
#                 [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_edit_{session.expense_id}")]
#             ]
#            
#             await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
#             return
#            
#         await query.edit_message_text(
#             message,
#             reply_markup=InlineKeyboardMarkup([[
#                 InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_edit_{session.expense_id}")
#             ]])
#         )
#        
#     except Exception as e:
#         logger.error(f"❌ Edit field callback error: {e}")
#         await query.edit_message_text("❌ Error starting field edit. Please try again.")

async def enhanced_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced message handler that checks for active edit or add category sessions"""
    try:
        user_id = update.effective_user.id
        message_text = update.message.text
        
        # Check if user is in an edit expense conversation
        if user_id in user_states and user_states[user_id].get('state') == EDITING_EXPENSE:
            await handle_edit_expense_conversation_input(update, context)
            return
        
        # Check if user is in an add category session
        if user_id in user_states and user_states[user_id].get('state') == ADDING_CATEGORY:
            await handle_add_category_input(update, context)
            return

        # Otherwise, use the regular text handler
        await handle_text(update, context)
        
    except Exception as e:
        logger.error(f"❌ Enhanced message handler error: {e}")
        await update.message.reply_text("😅 Something went wrong. Please try again!")

# NEW: handle_edit_expense_conversation_input for chat-based editing
async def handle_edit_expense_conversation_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text input during the chat-based expense editing conversation flow."""
    user_id = update.effective_user.id
    message_text = update.message.text
    user_state = user_states.get(user_id)

    if not user_state or user_state.get('state') != EDITING_EXPENSE:
        await update.message.reply_text("🤔 No active expense editing session. Please start editing from the approval message.")
        return

    session_id = user_state.get('session_id')
    session = edit_session_manager.sessions.get(session_id)

    if not session or session.is_expired():
        await update.message.reply_text("❌ Edit session expired. Please start editing again from the approval message.")
        del user_states[user_id]
        return

    current_step = user_state.get('step')
    
    # Keyboard for save/cancel after collecting info
    save_cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 Save Changes", callback_data=f"save_expense_{session.expense_id}"),
         InlineKeyboardButton("❌ Cancel Edit", callback_data=f"cancel_edit_conversation_{session.expense_id}")]
    ])
    
    cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Edit", callback_data=f"cancel_edit_conversation_{session.expense_id}")]
    ])

    try:
        if current_step == EDITING_AMOUNT:
            try:
                amount = float(re.findall(r'\d+(?:\.\d+)?', message_text)[0])
                if amount <= 0:
                    raise ValueError("Amount must be positive.")
                session.update_field('amount', amount, f"User updated amount via chat: {message_text}")
                
                user_states[user_id]['step'] = EDITING_CATEGORY
                await update.message.reply_text(
                    f"✅ Amount updated to ₹{amount}. Now, please send the **new category** for this expense (e.g., 'Groceries', 'Utilities').",
                    reply_markup=cancel_keyboard
                )
            except (ValueError, IndexError):
                await update.message.reply_text(
                    "❌ Invalid amount format. Please send just the number (e.g., 350 or 350.50):",
                    reply_markup=cancel_keyboard
                )
                return

        elif current_step == EDITING_CATEGORY:
            new_category = message_text.strip().lower()
            categories = get_user_categories(user_id)

            if not new_category:
                await update.message.reply_text("Category cannot be empty. Please send a valid category name:", reply_markup=cancel_keyboard)
                return

            # Optionally, you could use AI to suggest closest category if not exact match
            if new_category not in categories:
                # Simple AI suggestion for non-existent category
                ai_cat_prompt = f"The user entered '{new_category}' as a category. Their existing categories are: {list(categories.keys())}. Is '{new_category}' a reasonable new category, or is there a very close existing category? Respond with ONLY the most appropriate existing category name (lowercase) or 'NEW_CATEGORY' if it's genuinely new and reasonable."
                ai_response = model.generate_content(ai_cat_prompt).text.strip()
                
                if ai_response.lower() in categories:
                    suggested_cat = ai_response.lower()
                    await update.message.reply_text(
                        f"🤔 I don't recognize '{new_category.title()}', but perhaps you meant '{suggested_cat.title()}'? "
                        f"If so, I'll use '{suggested_cat.title()}'. Otherwise, I'll add '{new_category.title()}' as a new category. "
                        "Confirm by typing 'yes' or send a different category name."
                    )
                    user_states[user_id]['pending_category_confirmation'] = new_category
                    user_states[user_id]['suggested_category'] = suggested_cat
                    return
                elif ai_response.lower() == 'new_category':
                    # Add as new category
                    categories[new_category] = {'emoji': '📝', 'keywords': []} # Default emoji/keywords
                    save_categories()
                    session.update_field('category', new_category, f"User added new category via chat: {new_category}")
                    await update.message.reply_text(f"✅ Category '{new_category.title()}' added as a new category and updated for this expense!", reply_markup=save_cancel_keyboard)
                    del user_states[user_id] # Clear state after completion
                    return
                else:
                    # Fallback if AI doesn't give clear existing or new_category
                    categories[new_category] = {'emoji': '📝', 'keywords': []} # Default emoji/keywords
                    save_categories()
                    session.update_field('category', new_category, f"User added new category via chat: {new_category}")
                    await update.message.reply_text(f"✅ Category '{new_category.title()}' added as a new category and updated for this expense!", reply_markup=save_cancel_keyboard)
                    del user_states[user_id] # Clear state after completion
                    return
            else:
                session.update_field('category', new_category, f"User updated category via chat: {new_category}")
                await update.message.reply_text(f"✅ Category updated to '{new_category.title()}'.", reply_markup=save_cancel_keyboard)
                del user_states[user_id] # Clear state after completion
                return
        
        # Handle confirmation for suggested category
        elif 'pending_category_confirmation' in user_states[user_id] and message_text.lower() == 'yes':
            confirmed_category = user_states[user_id]['suggested_category']
            session.update_field('category', confirmed_category, f"User confirmed suggested category via chat: {confirmed_category}")
            await update.message.reply_text(f"✅ Category updated to '{confirmed_category.title()}'.", reply_markup=save_cancel_keyboard)
            del user_states[user_id] # Clear state after completion
            return
        elif 'pending_category_confirmation' in user_states[user_id] and message_text.lower() != 'yes':
            # User rejected suggestion, treat original input as new category
            original_input = user_states[user_id]['pending_category_confirmation']
            categories = get_user_categories(user_id)
            categories[original_input] = {'emoji': '📝', 'keywords': []} # Add as new category
            save_categories()
            session.update_field('category', original_input, f"User added new category (rejected suggestion) via chat: {original_input}")
            await update.message.reply_text(f"✅ Category '{original_input.title()}' added as a new category and updated for this expense!", reply_markup=save_cancel_keyboard)
            del user_states[user_id] # Clear state after completion
            return


    except Exception as e:
        logger.error(f"❌ handle_edit_expense_conversation_input error at step {current_step}: {e}\n{traceback.format_exc()}")
        await update.message.reply_text("❌ Error processing your input for editing. Please try again or type '/start' to reset.", reply_markup=cancel_keyboard)
        if user_id in user_states:
            del user_states[user_id]


async def handle_expense_callback(query, data):
    """Handle expense approval callbacks"""
    try:
        action, expense_id = data.split('_', 1)
        
        if expense_id not in pending_expenses:
            await query.edit_message_text("❌ Expense session expired. Please send the expense again.")
            return
        
        expense_data = pending_expenses[expense_id]
        user_id = query.from_user.id
        
        if action == "approve":
            if add_expense_to_sheet(user_id, expense_data, "Approved"):
                # AI-generated success message
                success_response = await GeminiDecisionEngine.generate_smart_response(
                    f"Expense approved: ₹{expense_data['amount']} for {expense_data.get('merchant', 'Unknown')}",
                    {"intent": "expense", "action": "approved"},
                    expense_data
                )
                
                await query.edit_message_text(
                    f"✅ **{success_response}**\n\n"
                    f"💰 ₹{expense_data['amount']} - {expense_data.get('merchant', 'Unknown')}\n"
                    f"📂 {expense_data['category'].title()}"
                )
            else:
                await query.edit_message_text("❌ Error saving to Google Sheet. Please try again.")
            del pending_expenses[expense_id]
            
        elif action == "edit":
            # Create a new edit session for this expense
            session = edit_session_manager.create_session(user_id, expense_id, expense_data)
            
            # Set user state for chat-based editing
            user_states[user_id] = {'state': EDITING_EXPENSE, 'step': EDITING_AMOUNT, 'session_id': session.session_id}
            
            # Prompt for amount directly
            await query.edit_message_text(
                f"✏️ **Editing Expense:**\n\n"
                f"💰 Current Amount: ₹{session.expense_data['amount']}\n"
                f"📂 Current Category: {session.expense_data['category'].title()}\n\n"
                f"Please send the **new amount** for this expense (e.g., '350' or '49.99').",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel Edit", callback_data=f"cancel_edit_conversation_{expense_id}")
                ]])
            )
            
        elif action == "reject":
            del pending_expenses[expense_id]
            reject_response = await GeminiDecisionEngine.generate_smart_response(
                "User rejected an expense",
                {"intent": "expense", "action": "rejected"},
                expense_data
            )
            await query.edit_message_text(f"❌ {reject_response}")
            
    except Exception as e:
        logger.error(f"❌ Expense callback error: {e}")
        await query.edit_message_text("❌ Error processing expense. Please try again.")

async def handle_save_expense_callback(query, data):
    """Save edited expense to Google Sheet."""
    try:
        user_id = query.from_user.id
        # Extract expense_id from data like "save_expense_exp_user_timestamp"
        expense_id = data.replace("save_expense_", "")

        session = edit_session_manager.get_session(user_id)
        if not session or session.expense_id != expense_id:
            await query.edit_message_text("❌ Edit session expired or invalid. Please try again.")
            return

        edited_data = session.expense_data
        if add_expense_to_sheet(user_id, edited_data, "Edited & Approved"):
            success_response = await GeminiDecisionEngine.generate_smart_response(
                f"Edited expense saved: ₹{edited_data['amount']} for {edited_data.get('merchant', 'Unknown')}",
                {"intent": "expense", "action": "edited_and_saved"},
                edited_data
            )
            
            await query.edit_message_text(
                f"✅ **{success_response}**\n\n"
                f"💰 ₹{edited_data['amount']} - {edited_data.get('merchant', 'Unknown')}\n"
                f"📂 {edited_data['category'].title()}"
            )
        else:
            await query.edit_message_text("❌ Error saving edited expense to Google Sheet. Please try again.")
        
        edit_session_manager.cleanup_session(session.session_id) # Clean up session after saving
        if expense_id in pending_expenses:
            del pending_expenses[expense_id] # Also remove from pending if it was there
        if user_id in user_states:
            del user_states[user_id] # Clear user state

    except Exception as e:
        logger.error(f"❌ handle_save_expense_callback error: {e}\n{traceback.format_exc()}")
        await query.edit_message_text("❌ Error saving edited expense. Please try again.")

async def handle_ai_category_suggestions(query):
    """AI-powered category suggestions"""
    try:
        user_id = query.from_user.id
        current_categories = get_user_categories(user_id)
        
        suggestions_prompt = f"""
The user has these expense categories: {list(current_categories.keys())}

Suggest 3-5 additional useful expense categories that would be relevant for personal expense tracking.
Consider categories they might be missing.

Respond with JSON:
{{
    "suggestions": [
        {{"name": "category_name", "emoji": "📱", "keywords": ["keyword1", "keyword2"], "reason": "why useful"}},
        ...
    ]
}}
"""
        
        try:
            response = model.generate_content(suggestions_prompt)
            result = response.text.strip()
            
            if '```json' in result:
                result = result.split('```json')[1].split('```')[0].strip()
            elif '```' in result:
                result = result.split('```')[1].split('```')[0].strip()
            
            suggestions_data = json.loads(result)
            suggestions = suggestions_data.get('suggestions', [])
            
            if suggestions:
                message = "🤖 **AI Category Suggestions:**\n\n"
                keyboard = []
                
                # Filter out suggestions that already exist in user's categories
                filtered_suggestions = [
                    s for s in suggestions # FIX: Changed 's for s s' to 's for s'
                    if s['name'].lower() not in [k.lower() for k in current_categories.keys()]
                ][:5] # Limit to 5 new suggestions
                
                if not filtered_suggestions:
                    await query.edit_message_text("🤖 Your categories look comprehensive! No new suggestions at this time.")
                    return

                for i, suggestion in enumerate(filtered_suggestions):  
                    name = suggestion['name']
                    emoji = suggestion['emoji']
                    reason = suggestion['reason']
                    
                    message += f"{emoji} **{name.title()}**\n{reason}\n\n"
                    keyboard.append([InlineKeyboardButton(
                        f"Add {emoji} {name.title()}", 
                        callback_data=f"add_ai_cat_{i}" # Index refers to filtered_suggestions
                    )])
                
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_ai_cats")])
                
                # Store filtered suggestions temporarily for callback
                user_states[user_id] = {'ai_suggestions': filtered_suggestions}
                
                await query.edit_message_text(
                    message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.edit_message_text("🤖 Your categories look comprehensive! No additional suggestions at this time.")
                
        except Exception as e:
            logger.error(f"AI suggestions generation error: {e}")
            await query.edit_message_text("🤖 Error generating suggestions. Your current categories look good!")
            
    except Exception as e:
        logger.error(f"❌ AI category suggestions error: {e}")
        await query.edit_message_text("❌ Error generating AI suggestions.")

# Error handling wrapper
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler"""
    logger.error(f"❌ Exception while handling update {update}: {context.error}")
    logger.error(f"❌ Traceback: {traceback.format_exc()}")
    
    # Try to send user-friendly error message
    try:
        if update and hasattr(update, 'effective_message') and update.effective_message:
            await update.effective_message.reply_text(
                "😅 I encountered an unexpected error. Don't worry - I'm still learning! "
                "Please try again or contact support if the issue persists."
            )
    except Exception:
        logger.error("❌ Could not send error message to user")

def main():
    """Enhanced main function with better error handling"""
    try:
        # Fix encoding for Windows console
        import sys
        if sys.platform == "win32":
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
        
        print("🚀 Starting Enhanced Expense Bot with Gemini AI...")
        
        # Load data
        load_users()
        load_categories()
        
        # Validate environment
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        gemini_key = os.getenv('GEMINI_API_KEY')
        
        if not bot_token:
            raise ValueError("❌ TELEGRAM_BOT_TOKEN not found in .env file")
        if not gemini_key:
            raise ValueError("❌ GEMINI_API_KEY not found in .env file")
        
        print("✅ Environment variables validated")
        
        # Test Gemini connection
        try:
            test_response = model.generate_content("Hello, respond with 'Connection successful'")
            print(f"✅ Gemini AI test: {test_response.text.strip()}")
        except Exception as e:
            print(f"⚠️ Gemini test warning: {e}")
        
        # Test Google Sheets connection
        try:
            gc = get_google_client()
            if gc:
                print("✅ Google Sheets connection successful")
            else:
                print("⚠️ Google Sheets connection failed - check service account file")
        except Exception as e:
            print(f"⚠️ Google Sheets test warning: {e}")
        
        # Create application
        app = Application.builder().token(bot_token).build()
        
        # Clear any existing webhooks to prevent conflicts
        try:
            import asyncio
            # Use asyncio to run the async webhook deletion
            asyncio.create_task(app.bot.delete_webhook(drop_pending_updates=True))
            print("🧹 Cleared any existing webhooks")
        except Exception as e:
            print(f"⚠️ Webhook cleanup warning: {e}")
        
        # Add error handler
        app.add_error_handler(error_handler)
        
        # Add handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("sheet", get_sheet_link))
        app.add_handler(CommandHandler("categories", manage_categories))
        app.add_handler(CommandHandler("summary", monthly_summary))
        
        # Enhanced Message handlers
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, enhanced_handle_message))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        app.add_handler(CallbackQueryHandler(handle_robust_callback))
        
        # REMOVED: The MessageHandler for "⚙️ Manage Categories" is removed
        # as it caused a NameError and the "📂 Categories" button handles this flow.
        # app.add_handler(MessageHandler(filters.Regex("⚙️ Manage Categories"), handle_manage_categories))
        
        print("✅ All handlers registered")
        print("🎯 Bot Features:")
        print("   • Gemini AI for ALL user interactions")
        print("   • Smart intent detection and response")
        print("   • Enhanced expense parsing")
        print("   • Improved OCR for screenshots")
        print("   • AI-powered category suggestions")
        print("   • Comprehensive error handling")
        print("   • Detailed logging and monitoring")
        
        print("\n🚀 Enhanced Expense Bot started successfully!")
        print("   The bot now uses Gemini AI to make ALL decisions about user interactions.")
        print("   Send any message and Gemini will determine the best response!\n")
        
        # Start polling
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        print(f"❌ Critical startup error: {e}")
        print(f"❌ Traceback: {traceback.format_exc()}")
        logger.error(f"❌ Startup failed: {e}")
        raise

if __name__ == '__main__':
    main()

