#!/usr/bin/env python3
"""
Test script to verify Gemini AI parsing works before running the full bot
"""

import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_gemini_parsing():
    """Test Gemini expense parsing"""
    try:
        genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        test_cases = [
            "Coffee $4.50 at Starbucks with credit card",
            "Lunch 15 dollars McDonald's cash",
            "Gas station $45.20 Shell debit card yesterday",
            "Grocery shopping $67.89 Walmart"
        ]
        
        for i, test_text in enumerate(test_cases, 1):
            print(f"\n--- Test {i} ---")
            print(f"Input: {test_text}")
            
            prompt = f"""Parse this expense text into JSON format:
"{test_text}"

Return ONLY valid JSON with these exact fields:
{{
  "amount": number,
  "category": "string",
  "description": "string", 
  "payment_method": "string",
  "date": "YYYY-MM-DD"
}}

Use today's date if no date is mentioned.
Amount should be a number without currency symbols."""

            response = model.generate_content(prompt)
            result = response.text.strip()
            
            print(f"Gemini Response: {result}")
            
            # Clean up response (Gemini sometimes adds extra text)
            clean_result = result
            if '```json' in result:
                clean_result = result.split('```json')[1].split('```')[0].strip()
            elif '```' in result:
                clean_result = result.split('```')[1].split('```')[0].strip()
            
            try:
                parsed = json.loads(clean_result)
                print("Valid JSON!")
                print(f"Amount: ${parsed['amount']}")
                print(f"Category: {parsed['category']}")
                print(f"Description: {parsed['description']}")
                print(f"Payment: {parsed['payment_method']}")
                print(f"Date: {parsed['date']}")
            except json.JSONDecodeError:
                print("Invalid JSON returned by Gemini")
                print(f"Cleaned result: {clean_result}")
                
    except Exception as e:
        print(f"Error: {e}")
        print("Check your GEMINI_API_KEY in .env file")

if __name__ == "__main__":
    print("Testing Gemini AI Expense Parsing...")
    test_gemini_parsing()
    print("\nTest complete!")
