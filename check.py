lines = open('app.py', encoding='utf-8').readlines()
# 173~183번째 줄 (0-indexed: 172~182) 교체
new_lines = [
    '    # Yahoo Finance fallback (direct API)\n',
    '    try:\n',
    '        import requests as _req, pandas as _pd\n',
    '        _url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"\n',
    '        _params = {"interval": "1d", "range": "30d"}\n',
    '        _headers = {"User-Agent": "Mozilla/5.0"}\n',
    '        _r = _req.get(_url, params=_params, headers=_headers, timeout=10)\n',
    '        _j = _r.json()["chart"]["result"][0]\n',
    '        _ts = _pd.to_datetime(_j["timestamp"], unit="s").normalize()\n',
    '        _close = _j["indicators"]["quote"][0]["close"]\n',
    '        yf_spx = _pd.Series(_close, index=_ts, name="SP500")\n',
    '        missing = yf_spx.index.difference(spx_d.index)\n',
    '        if len(missing) > 0:\n',
    '            spx_d = _pd.concat([spx_d, yf_spx.loc[missing]]).sort_index()\n',
    '    except Exception as yf_err:\n',
    '        db_set("yf_error", str(yf_err))\n',
]
print(f"replacing lines 173-183:")
for i in range(172, 183):
    print(f"  {i+1}: {lines[i].rstrip()}")
