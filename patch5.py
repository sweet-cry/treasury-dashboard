content = open('app.py', encoding='utf-8').read()
old = '@app.route("/api/cron/tic")'
new = '@app.route("/api/debug")\ndef debug_info():\n    return jsonify({"yf_error": db_get("yf_error"), "nl_error": db_get("nl_error")})\n\n@app.route("/api/cron/tic")'
assert old in content, "MATCH FAILED"
open('app.py', 'w', encoding='utf-8').write(content.replace(old, new, 1))
print('OK')
