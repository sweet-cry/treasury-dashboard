"""
Net Liquidity + 국가별 미국채 보유 Dashboard
=============================================
환경변수:
  FRED_API_KEY     : FRED API Key (필수)
  REFRESH_INTERVAL : 갱신 주기 초 (기본 3600)
  START_DATE       : 시작일 (기본 2000-01-01)
  PORT             : Railway 자동 설정
"""

import os
import io
import re
import threading
import time
import requests as req
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from flask import Flask, render_template_string
from datetime import datetime

API_KEY          = os.environ.get("FRED_API_KEY", "")
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "3600"))
START_DATE       = os.environ.get("START_DATE", "2000-01-01")
PORT             = int(os.environ.get("PORT", "5000"))

TIC_URL = "https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/mfhhis01.txt"
TIC_COUNTRIES = ["Japan", "China, Mainland", "United Kingdom", "Luxembourg",
                 "Cayman Islands", "Canada", "Belgium", "Ireland",
                 "France", "Switzerland", "Taiwan", "India", "Brazil"]
TIC_COLORS = {
    "Japan": "#1f77b4", "China, Mainland": "#d62728", "United Kingdom": "#2ca02c",
    "Luxembourg": "#ff7f0e", "Cayman Islands": "#9467bd", "Canada": "#8c564b",
    "Belgium": "#e377c2", "Ireland": "#7f7f7f", "France": "#bcbd22",
    "Switzerland": "#17becf", "Taiwan": "#aec7e8", "India": "#ffbb78", "Brazil": "#98df8a",
}

app = Flask(__name__)
cache = {
    "chart1_html": None, "chart2_html": None,
    "summary": None, "table_rows": None,
    "model_info": None, "updated_at": None, "error": None,
    "tic_chart_html": None, "tic_table": None,
    "tic_updated_at": None, "tic_error": None,
}
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Fed Dashboard</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0;}
    body{font-family:Arial,'Segoe UI',sans-serif;background:#f0f0f0;color:#1a1a1a;}
    .header{background:#fff;border-bottom:2px solid #cc0000;padding:11px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;}
    .header h1{font-size:15px;font-weight:700;color:#333;}
    .badge{display:inline-block;font-size:10px;background:#cc0000;color:#fff;border-radius:2px;padding:1px 6px;margin-left:8px;font-weight:700;}
    .meta{font-size:11px;color:#666;}
    .refresh-btn{font-size:11px;padding:5px 12px;border:1px solid #cc0000;border-radius:2px;background:transparent;cursor:pointer;color:#cc0000;}
    .refresh-btn:hover{background:#fff0f0;}
    .tabs{display:flex;gap:0;padding:12px 20px 0;border-bottom:2px solid #cc0000;}
    .tab{padding:8px 20px;font-size:12px;font-weight:700;cursor:pointer;background:#f0f0f0;color:#888;border:1px solid #ddd;border-bottom:none;border-radius:2px 2px 0 0;margin-right:4px;transition:all .15s;}
    .tab.active{background:#fff;color:#cc0000;border-bottom:2px solid #fff;margin-bottom:-2px;}
    .tab-content{display:none;padding:16px 20px;}
    .tab-content.active{display:block;}
    .container{max-width:1280px;margin:0 auto;}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-bottom:16px;}
    .mc{background:#fff;border-radius:2px;padding:12px 14px;border:1px solid #ddd;border-top:3px solid #cc0000;}
    .mc-lbl{font-size:10px;color:#777;margin-bottom:4px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;}
    .mc-val{font-size:19px;font-weight:700;color:#111;font-family:'Courier New',monospace;}
    .mc-sub{font-size:11px;margin-top:3px;}
    .pos{color:#2ca02c;}.neg{color:#d62728;}.neu{color:#888;}
    .chart-card{background:#fff;border:1px solid #ddd;border-radius:2px;overflow:hidden;margin-bottom:12px;}
    .chart-header{padding:10px 12px;border-bottom:2px solid #cc0000;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;}
    .chart-title{font-size:12px;font-weight:700;color:#333;margin-bottom:5px;}
    .legend{display:flex;gap:12px;font-size:11px;color:#444;flex-wrap:wrap;}
    .legend span{display:flex;align-items:center;gap:4px;}
    .zoom-btns{display:flex;gap:4px;}
    .zoom-btns button{font-size:11px;padding:3px 9px;border:1px solid #ddd;border-radius:2px;background:#f8f8f8;cursor:pointer;color:#555;}
    .zoom-btns button:hover{background:#fff0f0;color:#cc0000;}
    .section-title{font-size:11px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;padding-left:2px;}
    .method-box{background:#fff;border:1px solid #ddd;border-left:4px solid #cc0000;border-radius:2px;padding:16px 18px;margin-bottom:12px;font-size:12px;line-height:1.7;}
    .method-box h3{font-size:12px;font-weight:700;color:#cc0000;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px;}
    .method-box .formula{font-family:'Courier New',monospace;background:#f8f8f8;border:1px solid #eee;padding:8px 12px;border-radius:2px;margin:6px 0;font-size:12px;color:#333;}
    .method-box .desc{color:#555;margin:4px 0;}
    .method-box .warn{color:#888;font-size:11px;margin-top:8px;padding-top:8px;border-top:1px dashed #ddd;}
    .model-info{background:#f8f8f8;border:1px solid #eee;border-radius:2px;padding:8px 12px;margin-top:8px;font-family:'Courier New',monospace;font-size:11px;color:#555;}
    .tbl-wrap{background:#fff;border:1px solid #ddd;border-radius:2px;overflow-x:auto;margin-bottom:12px;}
    table{width:100%;border-collapse:collapse;font-size:12px;font-family:'Courier New',monospace;}
    thead tr{background:#cc0000;color:#fff;}
    thead th{padding:8px 12px;text-align:right;font-weight:700;font-size:11px;white-space:nowrap;}
    thead th:first-child,thead th:nth-child(2){text-align:left;}
    tbody tr:nth-child(even){background:#f9f9f9;}
    tbody tr:hover{background:#fff3f3;}
    tbody td{padding:7px 12px;text-align:right;border-bottom:1px solid #eee;white-space:nowrap;}
    tbody td:first-child,tbody td:nth-child(2){text-align:left;color:#555;}
    .badge-up{background:#e8f5e9;color:#2ca02c;padding:1px 5px;border-radius:2px;font-size:11px;}
    .badge-dn{background:#fff0f0;color:#d62728;padding:1px 5px;border-radius:2px;font-size:11px;}
    .summary-box{background:#fff;border:1px solid #ddd;border-top:3px solid #cc0000;border-radius:2px;padding:14px 18px;margin-bottom:12px;font-family:'Courier New',monospace;font-size:12px;}
    .summary-box .row{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #f0f0f0;}
    .summary-box .row:last-child{border-bottom:none;}
    .summary-box .lbl{color:#666;}
    .summary-box .val{font-weight:700;color:#111;}
    .divider{border:none;border-top:2px solid #cc0000;margin:4px 0 10px;}
    .bar-cell{display:flex;align-items:center;gap:6px;justify-content:flex-end;}
    .bar{height:8px;border-radius:2px;display:inline-block;}
    .info-box{background:#fff;border:1px solid #ddd;border-left:4px solid #cc0000;border-radius:2px;padding:12px 16px;font-size:12px;line-height:1.7;color:#555;margin-bottom:12px;}
    .error{background:#fff0f0;border:1px solid #cc0000;border-radius:2px;padding:14px;color:#cc0000;margin-bottom:12px;font-size:13px;}
    .loading{text-align:center;padding:60px;color:#888;font-size:14px;}
    .footer{font-size:10px;color:#aaa;text-align:center;padding:10px;border-top:1px solid #ddd;margin-top:4px;}
  </style>
  <script>
    let cd={{ refresh_interval }};
    function tick(){
      cd--;
      const el=document.getElementById('cd');
      if(el) el.textContent=Math.floor(cd/60)+'min '+String(cd%60).padStart(2,'0')+'s 후 자동갱신';
      if(cd<=0) location.reload();
      else setTimeout(tick,1000);
    }
    window.onload=function(){
      tick();
      {% if not summary and not error %}setTimeout(()=>location.reload(),10000);{% endif %}
    };
    function manualRefresh(){
      document.getElementById('cd').textContent='갱신 중...';
      fetch('/refresh').then(()=>setTimeout(()=>location.reload(),3000));
    }
    function switchTab(id){
      document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
      document.getElementById('tab-btn-'+id).classList.add('active');
      document.getElementById('tab-'+id).classList.add('active');
    }
    function getPlotlyDiv(cid){return document.getElementById(cid).querySelector('.js-plotly-plot');}
    function zoomChart(cid,dir){
      const el=getPlotlyDiv(cid); if(!el||!window.Plotly) return;
      const r=el.layout.xaxis.range; if(!r) return;
      const mid=(new Date(r[0]).getTime()+new Date(r[1]).getTime())/2;
      const half=(new Date(r[1]).getTime()-new Date(r[0]).getTime())/2;
      const f=dir==='in'?0.6:1.6;
      Plotly.relayout(el,{'xaxis.range':[new Date(mid-half*f).toISOString().slice(0,10),new Date(mid+half*f).toISOString().slice(0,10)]});
    }
    function resetChart(cid){
      const el=getPlotlyDiv(cid); if(!el||!window.Plotly) return;
      Plotly.relayout(el,{'xaxis.autorange':true,'yaxis.autorange':true});
    }
  </script>
</head>
<body>
<div class="header">
  <h1>Federal Reserve Dashboard <span class="badge">LIVE</span></h1>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
    <span class="meta" id="cd"></span>
    <span class="meta">Updated: {{ updated_at }}</span>
    <button class="refresh-btn" onclick="manualRefresh()">Refresh</button>
  </div>
</div>

<div class="container">
<div class="tabs">
  <div class="tab active" id="tab-btn-nl" onclick="switchTab('nl')">Net Liquidity</div>
  <div class="tab" id="tab-btn-tic" onclick="switchTab('tic')">국가별 미국채 보유</div>
</div>

<div id="tab-nl" class="tab-content active">
{% if error %}
  <div class="error">Error: {{ error }}</div>
{% elif not summary %}
  <div class="loading">FRED 데이터 로딩 중... 잠시 후 자동 새로고침됩니다.</div>
{% else %}

  <div class="metrics">
    <div class="mc"><div class="mc-lbl">Net Liquidity</div><div class="mc-val">{{ summary.nl }}</div><div class="mc-sub {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_chg }}</div></div>
    <div class="mc"><div class="mc-lbl">NL Regression FV</div><div class="mc-val">{{ summary.fv_nl }}</div><div class="mc-sub {{ 'pos' if summary.fv_nl_cheap else ('neg' if summary.fv_nl_cheap is not none else 'neu') }}">{{ summary.fv_nl_gap }}</div></div>
    <div class="mc"><div class="mc-lbl">WALCL <span style="font-weight:400;color:#bbb;">주간</span></div><div class="mc-val">{{ summary.walcl }}</div><div class="mc-sub neu">{{ summary.walcl_date }}</div></div>
    <div class="mc"><div class="mc-lbl">TGA <span style="font-weight:400;color:#bbb;">일간</span></div><div class="mc-val">{{ summary.tga }}</div><div class="mc-sub neu">{{ summary.tga_date }}</div></div>
    <div class="mc"><div class="mc-lbl">RRP <span style="font-weight:400;color:#bbb;">일간</span></div><div class="mc-val">{{ summary.rrp }}</div><div class="mc-sub neu">{{ summary.rrp_date }}</div></div>
    <div class="mc"><div class="mc-lbl">S&P 500</div><div class="mc-val">{{ summary.spx_raw }}</div><div class="mc-sub neu">{{ summary.base_date }}</div></div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">WALCL 구성: Net Liquidity · TGA · RRP — Daily (2000–present)</div>
      <div class="legend">
        <span><span style="width:14px;height:10px;background:rgba(31,119,180,0.60);border-radius:2px;display:inline-block;"></span>Net Liquidity</span>
        <span><span style="width:14px;height:10px;background:rgba(44,160,44,0.55);border-radius:2px;display:inline-block;"></span>TGA</span>
        <span><span style="width:14px;height:10px;background:rgba(255,127,14,0.55);border-radius:2px;display:inline-block;"></span>RRP</span>
        <span style="font-size:10px;color:#999;">음영: 경기침체</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c1','in')">+</button><button onclick="zoomChart('c1','out')">−</button><button onclick="resetChart('c1')">↺</button></div>
    </div>
    <div id="c1" style="padding:4px;">{{ chart1_html | safe }}</div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">S&P 500 vs NL Regression FV — Daily (2000–present)</div>
      <div class="legend">
        <span><span style="width:18px;height:3px;background:#333;display:inline-block;"></span>S&P 500</span>
        <span><span style="width:18px;height:2px;border-top:2px solid #1f77b4;display:inline-block;"></span>NL 회귀 FV</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c2','in')">+</button><button onclick="zoomChart('c2','out')">−</button><button onclick="resetChart('c2')">↺</button></div>
    </div>
    <div id="c2" style="padding:4px;">{{ chart2_html | safe }}</div>
  </div>

  <div class="section-title">계산 방법론</div>
  <div class="method-box">
    <h3>1. Net Liquidity</h3>
    <div class="formula">NL = WALCL − TGA − RRP</div>
    <div class="desc"><b>WALCL</b>: Fed 총자산 — 많을수록 시중에 돈이 많이 풀린 상태</div>
    <div class="desc"><b>TGA 차감</b>: 재무부가 Fed에 예치한 현금 — 시장에 풀리지 않은 돈</div>
    <div class="desc"><b>RRP 차감</b>: MMF 등이 Fed에 맡긴 역레포 잔액 — 시장 밖에 있는 돈</div>
    <div class="desc" style="margin-top:6px;">→ Michael Howell(CrossBorder Capital), Lyn Alden 등이 대중화. Fed 유동성이 실제로 시장에 얼마나 풀려있는지 측정.</div>
    <h3 style="margin-top:14px;">2. NL 회귀 공정가치</h3>
    <div class="formula">SPX_FV = slope × NL + intercept</div>
    <div class="desc">2000년부터 현재까지 일간 데이터로 선형회귀. NL↑ → SPX 공정가치↑ 관계 모델링.</div>
    {% if model_info %}<div class="model-info">slope={{ model_info.slope }} | intercept={{ model_info.intercept }} | R²={{ model_info.r2 }} | n={{ model_info.n }}</div>{% endif %}
    <h3 style="margin-top:14px;">3. 괴리율</h3>
    <div class="formula">괴리율 = (SPX현재가 − FV) / FV × 100 (%)</div>
    <div class="desc">양수(+): 고평가 &nbsp;|&nbsp; 음수(−): 저평가</div>
    <div class="warn">※ NL↔SPX 상관관계(R²≈0.6~0.8)는 표본 기간에 의존하며, 인과관계가 아닌 상관관계입니다. 절대적 FV보다 <b>방향성·괴리 추세</b> 위주로 활용 권장.</div>
  </div>

  <div class="section-title">요약</div>
  <div class="summary-box">
    <div class="row"><span class="lbl">기준일</span><span class="val">{{ summary.base_date }}</span></div>
    <div class="row"><span class="lbl">WALCL ({{ summary.walcl_date }})</span><span class="val">{{ summary.walcl_raw }}</span></div>
    <div class="row"><span class="lbl">TGA ({{ summary.tga_date }})</span><span class="val">{{ summary.tga_raw }}</span></div>
    <div class="row"><span class="lbl">RRP ({{ summary.rrp_date }})</span><span class="val">{{ summary.rrp_raw }}</span></div>
    <div class="row"><span class="lbl">Net Liquidity</span><span class="val {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_raw }} &nbsp;({{ summary.nl_chg }})</span></div>
    <hr class="divider">
    <div class="row"><span class="lbl">NL 회귀 공정가치</span><span class="val">{{ summary.fv_nl }}</span></div>
    <div class="row"><span class="lbl">SPX 현재가</span><span class="val {{ 'pos' if summary.fv_nl_cheap else 'neg' }}">{{ summary.spx_raw }} &nbsp;({{ summary.fv_nl_gap }})</span></div>
  </div>

  <div class="section-title">최근 10 영업일 데이터</div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>날짜</th><th style="text-align:right;">WALCL(B)</th><th style="text-align:right;">TGA(B)</th><th style="text-align:right;">RRP(B)</th><th style="text-align:right;">Net Liq(B)</th><th style="text-align:right;">DoD</th><th style="text-align:right;">SP500</th><th style="text-align:right;">NL FV</th><th style="text-align:right;">괴리율</th></tr></thead>
      <tbody>
        {% for row in table_rows %}
        <tr>
          <td>{{ row.date }}</td><td>{{ row.walcl }}</td><td>{{ row.tga }}</td><td>{{ row.rrp }}</td>
          <td><strong>{{ row.nl }}</strong></td>
          <td>{% if row.dod_pos is not none %}<span class="{{ 'badge-up' if row.dod_pos else 'badge-dn' }}">{{ row.dod }}</span>{% else %}—{% endif %}</td>
          <td>{{ row.spx }}</td><td>{{ row.fv_nl }}</td>
          <td>{% if row.gap is not none %}<span class="{{ 'badge-up' if row.gap_pos else 'badge-dn' }}">{{ row.gap }}</span>{% else %}—{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

{% endif %}
</div>

<div id="tab-tic" class="tab-content">
{% if tic_error %}
  <div class="error">TIC 데이터 오류: {{ tic_error }}</div>
{% elif not tic_chart_html %}
  <div class="loading">TIC 데이터 로딩 중...</div>
{% else %}

  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">주요국 미국채 보유량 — Monthly (2000–present)</div>
        <div class="legend">
          {% for c in tic_legend %}
          <span><span style="width:18px;height:3px;background:{{ c.color }};display:inline-block;"></span>{{ c.name }}</span>
          {% endfor %}
        </div>
      </div>
      <div class="zoom-btns"><button onclick="zoomChart('ctic','in')">+</button><button onclick="zoomChart('ctic','out')">−</button><button onclick="resetChart('ctic')">↺</button></div>
    </div>
    <div id="ctic" style="padding:4px;">{{ tic_chart_html | safe }}</div>
  </div>

  <div class="section-title">최신 보유량 순위 <span style="font-weight:400;color:#999;font-size:10px;">{{ tic_updated_at }} 기준 · 약 6주 후행 발표</span></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>#</th><th>국가</th><th style="text-align:right;">보유량 (B)</th><th style="text-align:right;">전월比</th><th style="text-align:right;">비중</th></tr></thead>
      <tbody>
        {% for row in tic_table %}
        <tr>
          <td style="color:#999;text-align:left;">{{ row.rank }}</td>
          <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{{ row.color }};margin-right:6px;"></span>{{ row.name }}</td>
          <td>{{ row.val }}</td>
          <td><span class="{{ 'badge-up' if row.chg_pos else 'badge-dn' }}">{{ row.chg }}</span></td>
          <td>
            <div class="bar-cell">
              <span class="bar" style="width:{{ row.bar_pct }}px;background:{{ row.color }};opacity:0.7;"></span>
              {{ row.pct }}%
            </div>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="info-box">
    <b style="color:#cc0000;">TIC 데이터란?</b><br>
    Treasury International Capital — 미 재무부가 매월 발표하는 외국인의 미국채 보유 현황. 중국·일본의 보유량 변화는 달러 패권 및 미국채 금리에 영향을 미치는 핵심 지표.<br><br>
    <b style="color:#555;">발표 일정 (매월 18일경):</b><br>
    &nbsp;· 1월 데이터 → 3월 18일 발표<br>
    &nbsp;· 2월 데이터 → 4월 18일 발표<br>
    &nbsp;· 3월 데이터 → 5월 18일 발표<br>
    &nbsp;· <i>이하 동일 — 항상 약 6주 후행</i><br><br>
    <b style="color:#555;">주의:</b> 보유량은 custodian 기준 — 중국 투자자가 벨기에 은행에 예탁 시 벨기에로 집계. 룩셈부르크·케이맨·벨기에 등 금융 허브의 높은 수치는 실제 해당국이 아닌 제3국 자금일 가능성이 높음.
  </div>

{% endif %}
</div>

  <div class="footer">
    Net Liquidity: FRED (WALCL·WDTGAL·RRPONTSYD·SP500) &nbsp;|&nbsp; 국가별 미국채: U.S. Treasury TIC &nbsp;|&nbsp; 2000–present
  </div>
</div>
</body>
</html>
"""


def fetch_series(series_id, start, frequency="d"):
    params = dict(series_id=series_id, api_key=API_KEY, file_type="json",
                  observation_start=start, frequency=frequency)
    r = req.get(FRED_BASE, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error_message" in data:
        raise ValueError(f"{series_id}: {data['error_message']}")
    obs = [(o["date"], float(o["value"])) for o in data["observations"] if o["value"] != "."]
    if not obs:
        raise ValueError(f"{series_id}: 데이터 없음")
    s = pd.Series(dict(obs), name=series_id)
    s.index = pd.to_datetime(s.index)
    return s


def fetch_auto(series_id, start, preferred="d"):
    for freq in [preferred, "w", "bw", "m"]:
        try:
            s = fetch_series(series_id, start, frequency=freq)
            if len(s) > 0:
                print(f"  [{series_id}] freq={freq}")
                return s, freq
        except Exception:
            continue
    raise ValueError(f"{series_id}: 사용 가능한 frequency 없음")


def build_nl_data():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] WALCL...")
    walcl_w = fetch_series("WALCL", START_DATE, frequency="w")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] WDTGAL...")
    tga_d, _ = fetch_auto("WDTGAL", START_DATE, preferred="d")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] RRPONTSYD...")
    rrp_d, _ = fetch_auto("RRPONTSYD", START_DATE, preferred="d")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] SP500...")
    try:
        spx_d, _ = fetch_auto("SP500", START_DATE, preferred="d")
    except Exception:
        spx_d = pd.Series(dtype=float, name="SP500")

    df = pd.DataFrame({"TGA": tga_d}).sort_index()
    df["RRP"]   = rrp_d.reindex(df.index, method="ffill")
    df["WALCL"] = walcl_w.reindex(df.index, method="ffill")
    df["SP500"] = spx_d.reindex(df.index, method="ffill")
    df = df.dropna(subset=["TGA", "RRP", "WALCL"])
    df["NL"] = df["WALCL"] - df["TGA"] - df["RRP"]
    df["NL_DoD"] = df["NL"].diff()

    valid = df[["NL", "SP500"]].dropna()
    model_info = None
    if len(valid) >= 10:
        x, y = valid["NL"].values, valid["SP500"].values
        slope, intercept = np.polyfit(x, y, 1)
        r2 = np.corrcoef(x, y)[0, 1] ** 2
        print(f"  회귀 R²={r2:.3f}")
        df["FV_NL"] = slope * df["NL"] + intercept
        model_info = {"slope": f"{slope:.5f}", "intercept": f"{intercept:.1f}",
                      "r2": f"{r2:.3f}", "n": f"{len(valid):,}"}
    else:
        df["FV_NL"] = np.nan

    print(f"[{datetime.now().strftime('%H:%M:%S')}] NL 완료: {len(df)}개")
    return df, model_info


def fetch_tic_data():
    r = req.get(TIC_URL, timeout=30)
    r.raise_for_status()
    text = r.text

    records = []
    current_year = None

    for line in text.splitlines():
        # 탭 구분자로 분리
        parts = [p.strip() for p in line.split("\t")]
        parts = [p for p in parts if p]

        # 연도 헤더 행: Country + 연도들
        if parts and parts[0] == "Country":
            years = [p for p in parts[1:] if re.match(r"^\d{4}$", p)]
            if years:
                current_year = int(years[0])
            continue

        if current_year is None:
            continue

        if not parts:
            continue

        # 국가명 매칭
        raw_name = parts[0].strip('"').strip()
        for country in TIC_COUNTRIES:
            clean = country.replace('"', '').strip()
            if raw_name == clean:
                nums = []
                for p in parts[1:]:
                    try:
                        nums.append(float(p.replace(',', '')))
                    except ValueError:
                        pass
                if len(nums) >= 12:
                    for m_idx, v in enumerate(nums[:12]):
                        month_num = 12 - m_idx
                        records.append({
                            "date": pd.to_datetime(f"{current_year}-{month_num:02d}-01"),
                            "country": clean,
                            "value": v
                        })
                break

    if not records:
        raise ValueError("TIC 데이터 파싱 실패")

    df = pd.DataFrame(records)
    df = df.drop_duplicates(subset=["date","country"]).sort_values("date")
    pivot = df.pivot(index="date", columns="country", values="value").sort_index()
    pivot = pivot[pivot.index >= "2000-01-01"]
    print(f"TIC 완료: {len(pivot)}개 포인트, {len(pivot.columns)}개국")
    return pivot
def fmt_val(v):
    if abs(v) >= 1_000:
        return f"{v/1_000:.2f}T"
    return f"{v:,.0f}B"


def build_nl_summary(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    spx = latest["SP500"] if not pd.isna(latest["SP500"]) else None
    fv_nl = latest["FV_NL"] if "FV_NL" in latest.index and not pd.isna(latest["FV_NL"]) else None
    chg = latest["NL"] - prev["NL"] if prev is not None else 0

    walcl_date = df["WALCL"].last_valid_index()
    tga_date   = df["TGA"].last_valid_index()
    rrp_date   = df["RRP"].last_valid_index()

    fv_nl_gap = fv_nl_cheap = None
    if fv_nl and spx:
        gap = (spx - fv_nl) / fv_nl * 100
        fv_nl_gap = f"{'+' if gap>0 else ''}{gap:.1f}% {'고평가' if gap>0 else '저평가'}"
        fv_nl_cheap = gap < 0

    return {
        "base_date": df.index[-1].strftime("%Y-%m-%d"),
        "nl": fmt_val(latest["NL"]), "nl_raw": f"{latest['NL']:,.0f}B",
        "nl_chg": f"{'▲' if chg>=0 else '▼'} {fmt_val(abs(chg))} DoD", "nl_chg_pos": chg >= 0,
        "walcl": fmt_val(latest["WALCL"]), "walcl_raw": f"{latest['WALCL']:,.0f}B",
        "walcl_date": walcl_date.strftime("%m-%d") if walcl_date else "—",
        "tga": fmt_val(latest["TGA"]), "tga_raw": f"{latest['TGA']:,.0f}B",
        "tga_date": tga_date.strftime("%m-%d") if tga_date else "—",
        "rrp": fmt_val(latest["RRP"]), "rrp_raw": f"{latest['RRP']:,.0f}B",
        "rrp_date": rrp_date.strftime("%m-%d") if rrp_date else "—",
        "spx_raw": f"{spx:,.0f}" if spx else "—",
        "fv_nl": f"{fv_nl:,.0f}" if fv_nl else "—",
        "fv_nl_gap": fv_nl_gap or "데이터 부족", "fv_nl_cheap": fv_nl_cheap,
    }


def build_nl_table(df):
    tail = df.tail(11).copy()
    rows = []
    for i, (date, row) in enumerate(tail.iterrows()):
        prev_nl = tail.iloc[i-1]["NL"] if i > 0 else None
        dod = row["NL"] - prev_nl if prev_nl is not None else None
        spx = row["SP500"] if not pd.isna(row["SP500"]) else None
        fv_nl = row["FV_NL"] if "FV_NL" in row.index and not pd.isna(row["FV_NL"]) else None
        gap = gap_pos = None
        if spx and fv_nl:
            g = (spx - fv_nl) / fv_nl * 100
            gap = f"{'+' if g>0 else ''}{g:.1f}%"
            gap_pos = g < 0
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "walcl": f"{row['WALCL']:,.0f}", "tga": f"{row['TGA']:,.0f}", "rrp": f"{row['RRP']:,.0f}",
            "nl": f"{row['NL']:,.0f}",
            "dod": f"{'▲' if dod>=0 else '▼'}{abs(dod):,.0f}" if dod is not None else "—",
            "dod_pos": dod >= 0 if dod is not None else None,
            "spx": f"{spx:,.0f}" if spx else "—",
            "fv_nl": f"{fv_nl:,.0f}" if fv_nl else "—",
            "gap": gap, "gap_pos": gap_pos,
        })
    return list(reversed(rows[-10:]))


def build_chart1(df):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(180,0,0,0.07)", layer="below", line_width=0)
    # 스택 순서: RRP(바닥) → TGA(중간) → NL(상단) = WALCL 전체
    fig.add_trace(go.Scatter(x=df.index, y=df["RRP"], name="RRP",
        line=dict(color="#ff7f0e", width=0.8),
        fill="tozeroy", fillcolor="rgba(255,127,14,0.55)",
        stackgroup="walcl"))
    fig.add_trace(go.Scatter(x=df.index, y=df["TGA"], name="TGA",
        line=dict(color="#2ca02c", width=0.8),
        fill="tonexty", fillcolor="rgba(44,160,44,0.55)",
        stackgroup="walcl"))
    fig.add_trace(go.Scatter(x=df.index, y=df["NL"], name="Net Liquidity",
        line=dict(color="#1f77b4", width=1.5),
        fill="tonexty", fillcolor="rgba(31,119,180,0.60)",
        stackgroup="walcl"))
    grid = dict(showgrid=True, gridcolor="rgba(204,0,0,0.15)", gridwidth=0.5, griddash="dot",
                linecolor="#bbb", linewidth=1, showline=True, ticks="outside", tickcolor="#bbb",
                tickfont=dict(size=10, color="#555"))
    fig.update_layout(height=320, plot_bgcolor="#e8e8e8", paper_bgcolor="#ffffff",
        font=dict(family="Arial,sans-serif", size=11, color="#333"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Billions USD", title_font=dict(size=10, color="#555"),
                     tickformat=",", ticksuffix="B")
    return fig.to_html(include_plotlyjs="cdn", full_html=False, config={"displayModeBar": False})


def build_chart2(df):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(180,0,0,0.07)", layer="below", line_width=0)
    fig.add_trace(go.Scatter(x=df.index, y=df["SP500"], name="S&P 500",
        line=dict(color="#333333", width=2)))
    if "FV_NL" in df.columns and df["FV_NL"].notna().any():
        fig.add_trace(go.Scatter(x=df.index, y=df["FV_NL"], name="NL 회귀 FV",
            line=dict(color="#1f77b4", width=1.5)))
    spx_vals = df["SP500"].dropna()
    spx_min = int(spx_vals.min() * 0.9) if len(spx_vals) else 500
    spx_max = int(spx_vals.max() * 1.05) if len(spx_vals) else 7500
    grid = dict(showgrid=True, gridcolor="rgba(204,0,0,0.15)", gridwidth=0.5, griddash="dot",
                linecolor="#bbb", linewidth=1, showline=True, ticks="outside", tickcolor="#bbb",
                tickfont=dict(size=10, color="#555"))
    fig.update_layout(height=320, plot_bgcolor="#e8e8e8", paper_bgcolor="#ffffff",
        font=dict(family="Arial,sans-serif", size=11, color="#333"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Index Level", title_font=dict(size=10, color="#555"),
                     tickformat=",", range=[spx_min, spx_max])
    return fig.to_html(include_plotlyjs=False, full_html=False, config={"displayModeBar": False})


def build_tic_chart(pivot):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(180,0,0,0.07)", layer="below", line_width=0)
    for country in TIC_COUNTRIES:
        clean = country.replace('"','')
        if clean not in pivot.columns:
            continue
        color = TIC_COLORS.get(clean, "#888888")
        dash = "dash" if clean in ["Luxembourg","Cayman Islands","Canada","Belgium"] else "solid"
        fig.add_trace(go.Scatter(x=pivot.index, y=pivot[clean], name=clean,
            line=dict(color=color, width=1.8, dash=dash)))
    grid = dict(showgrid=True, gridcolor="rgba(204,0,0,0.15)", gridwidth=0.5, griddash="dot",
                linecolor="#bbb", linewidth=1, showline=True, ticks="outside", tickcolor="#bbb",
                tickfont=dict(size=10, color="#555"))
    fig.update_layout(height=380, plot_bgcolor="#e8e8e8", paper_bgcolor="#ffffff",
        font=dict(family="Arial,sans-serif", size=11, color="#333"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Billions USD", title_font=dict(size=10, color="#555"), tickformat=",")
    return fig.to_html(include_plotlyjs=False, full_html=False, config={"displayModeBar": False})


def build_tic_table(pivot):
    latest = pivot.iloc[-1].dropna().sort_values(ascending=False)
    prev = pivot.iloc[-2].dropna() if len(pivot) > 1 else None
    total = latest.sum()
    max_val = latest.max()
    rows = []
    for i, (country, val) in enumerate(latest.items()):
        chg = val - prev[country] if prev is not None and country in prev else None
        pct = val / total * 100 if total > 0 else 0
        bar_pct = int(val / max_val * 80)
        rows.append({
            "rank": i+1, "name": country,
            "color": TIC_COLORS.get(country, "#888"),
            "val": f"{val:,.1f}",
            "chg": f"{'+' if chg and chg>=0 else ''}{chg:.1f}" if chg is not None else "—",
            "chg_pos": chg >= 0 if chg is not None else True,
            "pct": f"{pct:.1f}", "bar_pct": bar_pct,
        })
    return rows[:15]


def refresh_data():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] NL 갱신 시작...")
    try:
        df, model_info = build_nl_data()
        cache["summary"] = build_nl_summary(df)
        cache["chart1_html"] = build_chart1(df)
        cache["chart2_html"] = build_chart2(df)
        cache["table_rows"] = build_nl_table(df)
        cache["model_info"] = model_info
        cache["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cache["error"] = None
        print(f"[{datetime.now().strftime('%H:%M:%S')}] NL 완료")
    except Exception as e:
        cache["error"] = str(e)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] NL 오류: {e}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] TIC 갱신 시작...")
    try:
        pivot = fetch_tic_data()
        cache["tic_chart_html"] = build_tic_chart(pivot)
        cache["tic_table"] = build_tic_table(pivot)
        cache["tic_updated_at"] = pivot.index[-1].strftime("%Y-%m")
        cache["tic_error"] = None
        print(f"[{datetime.now().strftime('%H:%M:%S')}] TIC 완료")
    except Exception as e:
        cache["tic_error"] = str(e)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] TIC 오류: {e}")


def background_loop():
    refresh_data()
    while True:
        time.sleep(REFRESH_INTERVAL)
        refresh_data()


@app.route("/")
def index():
    tic_legend = [{"name": c.replace('"',''), "color": TIC_COLORS.get(c.replace('"',''), "#888")}
                  for c in TIC_COUNTRIES[:6]]
    return render_template_string(HTML_TEMPLATE,
        chart1_html=cache.get("chart1_html"),
        chart2_html=cache.get("chart2_html"),
        summary=cache["summary"],
        table_rows=cache["table_rows"] or [],
        updated_at=cache["updated_at"] or "—",
        error=cache["error"], refresh_interval=REFRESH_INTERVAL,
        model_info=cache["model_info"],
        tic_chart_html=cache.get("tic_chart_html"),
        tic_table=cache.get("tic_table") or [],
        tic_updated_at=cache.get("tic_updated_at") or "—",
        tic_error=cache.get("tic_error"),
        tic_legend=tic_legend)


@app.route("/refresh")
def manual_refresh():
    threading.Thread(target=refresh_data, daemon=True).start()
    return "ok"


@app.route("/health")
def health():
    return "ok"


threading.Thread(target=background_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
