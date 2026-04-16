"""Fix products.json - replace Chinese quotes with corner brackets in content values"""
import json

with open('data/products.json', 'r', encoding='utf-8') as f:
    raw = f.read()

# Replace Chinese left/right double quotes with corner brackets
fixed = raw.replace('\u201c', '\u300c').replace('\u201d', '\u300d')

with open('data/products.json', 'w', encoding='utf-8') as f:
    f.write(fixed)

# Verify
with open('data/products.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
    print(f"JSON fixed OK, {len(data['products'])} products loaded")