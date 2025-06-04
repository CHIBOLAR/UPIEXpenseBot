#!/usr/bin/env python3
"""
Simple syntax test for bot_enhanced.py
"""

import ast
import sys

def test_syntax(filename):
    """Test if the Python file has valid syntax"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            source = f.read()
        
        # Parse the AST to check for syntax errors
        ast.parse(source)
        print(f"SUCCESS: {filename} has valid syntax!")
        return True
        
    except SyntaxError as e:
        print(f"SYNTAX ERROR in {filename}:")
        print(f"   Line {e.lineno}: {e.text}")
        print(f"   Error: {e.msg}")
        return False
        
    except Exception as e:
        print(f"ERROR reading {filename}: {e}")
        return False

if __name__ == "__main__":
    success = test_syntax("bot_enhanced.py")
    sys.exit(0 if success else 1)
