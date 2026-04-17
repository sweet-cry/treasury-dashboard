lines = open('app.py', encoding='utf-8').readlines()
for i, line in enumerate(lines):
    if 'nl_summary_exists' in line:
        lines[i] = '    try:\n        db_set("debug_test", "ping")\n        db_test = db_get("debug_test")\n    except Exception as e:\n        db_test = str(e)\n    return jsonify({"yf_error": db_get("yf_error"), "nl_error": db_get("nl_error"), "nl_updated_at": db_get_updated_at("nl_summary"), "nl_summary_exists": db_get("nl_summary") is not None, "db_write_test": db_test})\n'
        print(f"patched line {i+1}")
        break
open('app.py', 'w', encoding='utf-8').write("".join(lines))
print('OK')
