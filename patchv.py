import json
data = json.load(open('vercel.json', encoding='utf-8'))
data['functions'] = {"api/index.py": {"maxDuration": 60}}
json.dump(data, open('vercel.json', 'w', encoding='utf-8'), indent=2)
print(json.dumps(data, indent=2))
