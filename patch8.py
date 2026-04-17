content = open('app.py', encoding='utf-8').read()
# yfinance 블록 전체를 Yahoo API로 교체
import re
old_pattern = r'    # yfinance fallback.*?db_set\("yf_error", str\(yf_err\)\)'
new_code = '''    # Yahoo Finance fallback (direct API)
    try:
        import requests as _req, pandas as _pd
        _url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
        _params = {"interval": "1d", "range": "30d"}
        _headers = {"User-Agent": "Mozilla/5.0"}
        _r = _req.get(_url, params=_params, headers=_headers, timeout=10)
        _j = _r.json()["chart"]["result"][0]
        _ts = _pd.to_datetime(_j["timestamp"], unit="s").normalize()
        _close = _j["indicators"]["quote"][0]["close"]
        yf_spx = _pd.Series(_close, index=_ts, name="SP500")
        missing = yf_spx.index.difference(spx_d.index)
        if len(missing) > 0:
            spx_d = _pd.concat([spx_d, yf_spx.loc[missing]]).sort_index()
    except Exception as yf_err:
        db_set("yf_error", str(yf_err))'''
result = re.sub(old_pattern, new_code, content, flags=re.DOTALL)
assert result != content, "NO CHANGE"
open('app.py', 'w', encoding='utf-8').write(result)
print('OK')
