import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
with open('debug_2026-10-07.html', encoding='utf-8') as f:
    html = f.read()

# Find hashed class names
hashed = set(re.findall(r'tupleWrapper___\w+', html))
print('Hashed class names:', hashed)

# Find li elements containing those hashed classes
for cls in hashed:
    pattern = f'<li[^>]*{re.escape(cls)}[^>]*>'
    matches = re.findall(pattern, html, re.I)
    print(f'li elements with {cls}:', len(matches))

# Captcha check
captcha_idx = html.lower().find('captcha')
if captcha_idx >= 0:
    print('Captcha context:', repr(html[max(0, captcha_idx-100):captcha_idx+200]))

# Check if there are actual bus results despite captcha
price_symbol = '\u20b9'
prices_in_body = re.findall(price_symbol + r'[\s\d,]+', html)
print('Rupee price strings found:', len(prices_in_body), prices_in_body[:5])
