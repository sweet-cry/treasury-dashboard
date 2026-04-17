import pytz
content = open('app.py', encoding='utf-8').read()
old = '    threading.Thread(target=run_refresh_nl, daemon=True).start()\n    return jsonify({"status": "started"})'
new = '    run_refresh_nl()\n    kst = __import__("datetime").datetime.now(__import__("pytz").timezone("Asia/Seoul"))\n    return jsonify({"status": "ok", "updated_at": kst.strftime("%Y-%m-%d %H:%M KST"), "next": "daily 09:00 KST / Wed 16:30 KST"})'
assert old in content, "MATCH FAILED"
open('app.py', 'w', encoding='utf-8').write(content.replace(old, new, 1))
print('OK')
