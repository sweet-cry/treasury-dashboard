import json
data = json.load(open('vercel.json', encoding='utf-8'))
data['crons'] = [
    {"path": "/api/cron/nl", "schedule": "0 0 * * *"},
    {"path": "/api/cron/nl", "schedule": "30 7 * * 4"},
    {"path": "/api/cron/tic", "schedule": "0 2 18 * *"}
]
json.dump(data, open('vercel.json', 'w', encoding='utf-8'), indent=2)
print('OK')
