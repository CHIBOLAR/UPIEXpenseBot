#!/usr/bin/env python3
"""
Quick test script for Telegram Bot dependencies
"""
import sys
import os

def test_dependencies():
    print("Testing Telegram Bot Dependencies...")
    print("=" * 50)
    
    # Test Python version
    print(f"Python Version: {sys.version}")
    
    # Test imports
    try:
        import telegram
        print(f"[OK] python-telegram-bot: {telegram.__version__}")
    except ImportError as e:
        print(f"[FAIL] python-telegram-bot: {e}")
        return False
    
    try:
        import google.generativeai as genai
        print("[OK] google-generativeai: Imported successfully")
    except ImportError as e:
        print(f"[FAIL] google-generativeai: {e}")
        return False
    
    try:
        import gspread
        print(f"[OK] gspread: {gspread.__version__}")
    except ImportError as e:
        print(f"[FAIL] gspread: {e}")
        return False
    
    try:
        import PIL
        print(f"[OK] Pillow: {PIL.__version__}")
    except ImportError as e:
        print(f"[FAIL] Pillow: {e}")
        return False
    
    try:
        import dotenv
        print("[OK] python-dotenv: Imported successfully")
    except ImportError as e:
        print(f"[FAIL] python-dotenv: {e}")
        return False
    
    # Test .env file
    if os.path.exists('.env'):
        print("[OK] .env file: Found")
        from dotenv import load_dotenv
        load_dotenv()
        
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        gemini_key = os.getenv('GEMINI_API_KEY')
        
        if bot_token:
            print("[OK] TELEGRAM_BOT_TOKEN: Configured")
        else:
            print("[FAIL] TELEGRAM_BOT_TOKEN: Missing")
            
        if gemini_key:
            print("[OK] GEMINI_API_KEY: Configured")
        else:
            print("[FAIL] GEMINI_API_KEY: Missing")
    else:
        print("[FAIL] .env file: Not found")
    
    # Test service account
    if os.path.exists('telegram_service_account.json'):
        print("[OK] Google Service Account: Found")
    else:
        print("[WARN] Google Service Account: Not found (Sheets won't work)")
    
    print("=" * 50)
    print("SUCCESS: All core dependencies are working!")
    print("Your bot should run without issues!")
    return True

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    test_dependencies()
    input("\nPress Enter to exit...")
