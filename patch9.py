lines = open('app.py', encoding='utf-8').readlines()
# 184번 줄 (0-indexed 183) _close 라인 교체
lines[183] = '        _close = [x if x is not None else float("nan") for x in _j["indicators"]["quote"][0]["close"]]\n'
# 185번 줄 (0-indexed 184) Series 생성 후 dropna
lines[184] = '        yf_spx = _pd.Series(_close, index=_ts, name="SP500").dropna()\n'
open('app.py', 'w', encoding='utf-8').write("".join(lines))
print('OK')
