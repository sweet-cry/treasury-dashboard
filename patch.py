content = open('app.py', encoding='utf-8').read()
old = '    try:\n        spx_d, _ = fetch_auto("SP500", START_DATE, preferred="d")\n    except Exception:\n        spx_d = pd.Series(dtype=float, name="SP500")'
new = '    try:\n        spx_d, _ = fetch_auto("SP500", START_DATE, preferred="d")\n    except Exception:\n        spx_d = pd.Series(dtype=float, name="SP500")\n\n    # yfinance fallback\n    try:\n        import yfinance as yf\n        yf_spx = yf.download("^GSPC", start=START_DATE, progress=False, auto_adjust=True)["Close"]\n        yf_spx.index = __import__("pandas").to_datetime(yf_spx.index).tz_localize(None)\n        yf_spx.name = "SP500"\n        missing = yf_spx.index.difference(spx_d.index)\n        if len(missing) > 0:\n            spx_d = __import__("pandas").concat([spx_d, yf_spx.loc[missing]]).sort_index()\n    except Exception:\n        pass'
assert old in content, "MATCH FAILED: " + repr(content[content.find("SP500"):content.find("SP500")+200])
open('app.py', 'w', encoding='utf-8').write(content.replace(old, new, 1))
print('OK')
