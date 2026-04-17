content = open('app.py', encoding='utf-8').read()
old = '        yf_spx = yf.download(\"^GSPC\", start=START_DATE, progress=False, auto_adjust=True)[\"Close\"]'
new = '        _yf_raw = yf.download(\"^GSPC\", start=START_DATE, progress=False, auto_adjust=True)\n        yf_spx = _yf_raw[\"Close\"].squeeze()'
assert old in content, "MATCH FAILED"
open('app.py', 'w', encoding='utf-8').write(content.replace(old, new, 1))
print('OK')
