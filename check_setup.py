#!/usr/bin/env python3
"""
Check all dependencies and configuration for the Telegram bot
"""

import sys
import os
import importlib.util

def check_dependency(module_name, import_name=None):
    """Check if a Python module is installed"""
    if import_name is None:
        import_name = module_name
    
    try:
        if importlib.util.find_spec(import_name):
            print(f"OK: {module_name}")
            return True
        else:
            print(f"MISSING: {module_name}")
            return False
    except ImportError:
        print(f"ERROR: {module_name}")
        return False

def check_file(filepath):
    """Check if a file exists"""
    if os.path.exists(filepath):
        print(f"EXISTS: {filepath}")
        return True
    else:
        print(f"MISSING: {filepath}")
        return False

def main():
    print("=== TELEGRAM BOT SETUP VERIFICATION ===")
    print()
    
    # Check Python version
    print(f"Python version: {sys.version}")
    print()
    
    # Check required files
    print("--- FILES ---")
    files_ok = True
    files_ok &= check_file("C:/telegram4.0/bot_enhanced.py")
    files_ok &= check_file("C:/telegram4.0/.env")
    files_ok &= check_file("C:/telegram4.0/telegram_service_account.json")
    files_ok &= check_file("C:/telegram4.0/categories.json")
    files_ok &= check_file("C:/telegram4.0/users.json")
    print()
    
    # Check Python dependencies
    print("--- PYTHON DEPENDENCIES ---")
    deps_ok = True
    deps_ok &= check_dependency("python-telegram-bot", "telegram")
    deps_ok &= check_dependency("google-generativeai", "google.generativeai")
    deps_ok &= check_dependency("gspread")
    deps_ok &= check_dependency("google-auth", "google.auth")
    deps_ok &= check_dependency("pillow", "PIL")
    deps_ok &= check_dependency("python-dotenv", "dotenv")
    deps_ok &= check_dependency("requests")
    print()
    
    # Check environment variables
    print("--- ENVIRONMENT VARIABLES ---")
    from dotenv import load_dotenv
    load_dotenv("C:/telegram4.0/.env")
    
    env_ok = True
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    gemini_key = os.getenv('GEMINI_API_KEY')
    
    if telegram_token:
        print("SET: TELEGRAM_BOT_TOKEN")
    else:
        print("MISSING: TELEGRAM_BOT_TOKEN")
        env_ok = False
        
    if gemini_key:
        print("SET: GEMINI_API_KEY")
    else:
        print("MISSING: GEMINI_API_KEY")
        env_ok = False
    
    print()
    
    # Overall status
    print("=== OVERALL STATUS ===")
    if files_ok and deps_ok and env_ok:
        print("SUCCESS: All checks passed - Bot ready to run!")
        return 0
    else:
        print("FAILED: Some checks failed - Fix issues above")
        return 1

if __name__ == "__main__":
    sys.exit(main())
