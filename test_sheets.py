#!/usr/bin/env python3
"""
Test Google Sheets connection
"""

import gspread
from google.oauth2.service_account import Credentials

def test_google_sheets():
    """Test Google Sheets API connection"""
    try:
        print("Testing Google Sheets connection...")
        
        # Initialize Google Sheets client
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file('C:/telegram4.0/telegram_service_account.json', scopes=scope)
        gc = gspread.authorize(creds)
        
        print("Google Sheets API connected successfully!")
        
        # Test creating a sheet
        print("Testing sheet creation...")
        test_sheet = gc.create("Test_Expense_Bot")
        sheet = test_sheet.sheet1
        
        # Add headers
        headers = ['Date', 'Amount', 'Category', 'Description', 'Payment Method']
        sheet.append_row(headers)
        
        # Add a test row
        test_row = ['2024-06-04', 5.50, 'Coffee', 'Test expense', 'Credit Card']
        sheet.append_row(test_row)
        
        print(f"Test sheet created: {test_sheet.url}")
        print("Headers and test data added successfully!")
        
        # Clean up - delete the test sheet
        gc.del_spreadsheet(test_sheet.id)
        print("Test sheet deleted (cleanup)")
        
        return True
        
    except Exception as e:
        print(f"Google Sheets test failed: {e}")
        return False

if __name__ == "__main__":
    test_google_sheets()
