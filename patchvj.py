import json
data = json.load(open('vercel.json', encoding='utf-8'))
data.pop('functions', None)
json.dump(data, open('vercel.json', 'w', encoding='utf-8'), indent=2)
print('OK')
