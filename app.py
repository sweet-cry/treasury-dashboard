"""
Net Liquidity + 援??蹂?誘멸뎅梨?蹂댁쑀 Dashboard
=============================================
?섍꼍蹂??
  FRED_API_KEY : FRED API Key (?꾩닔)
  START_DATE   : ?쒖옉??(湲곕낯 2000-01-01)
  PORT         : Railway ?먮룞 ?ㅼ젙

?낅뜲?댄듃 ?ㅼ?以?(KST):
  - RRP  (?쇨컙): 留ㅼ씪 00:30
  - SPX  (?쇨컙): 留ㅼ씪 07:00
  - WALCL/TGA (二쇨컙 H.4.1): 留ㅼ＜ 紐⑹슂??05:30
  - TIC  (?붽컙): 留ㅼ썡 18??02:00
"""

import os
import io
import re
import threading
import requests as req
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from flask import Flask, render_template_string
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

API_KEY    = os.environ.get("FRED_API_KEY", "")
START_DATE = os.environ.get("START_DATE", "2000-01-01")
PORT       = int(os.environ.get("PORT", "5000"))
KST        = pytz.timezone("Asia/Seoul")

TIC_URL_HIST = "https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/mfhhis01.txt"
TIC_URL_CURR = "https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/slt_table5.txt"
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
    "next_h41": None,
    "tic_chart_html": None, "tic_table": None,
    "tic_updated_at": None, "tic_error": None,
    # DTS
    "dts_deposits": None, "dts_withdrawals": None,
    "dts_balance": None, "dts_date": None, "dts_error": None,
    # QRA
    "qra_data": None, "qra_error": None,
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
    body{font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',sans-serif;background:#0c0c10;color:#e2e2e2;}
    .header{background:#0c0c10;border-bottom:1px solid rgba(255,255,255,0.06);padding:13px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;}
    .header h1{font-size:14px;font-weight:500;color:#fff;display:flex;align-items:center;gap:8px;}
    .nav-dot{width:7px;height:7px;border-radius:50%;background:#60a5fa;display:inline-block;}
    .badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:3px 9px;border-radius:20px;border:1px solid rgba(96,165,250,0.3);color:#60a5fa;font-weight:400;}
    .meta{font-size:11px;color:rgba(255,255,255,0.25);}
    .refresh-btn{font-size:11px;padding:5px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:6px;background:transparent;cursor:pointer;color:rgba(255,255,255,0.5);}
    .refresh-btn:hover{background:rgba(255,255,255,0.06);color:#fff;}
    .tabs{display:flex;gap:4px;padding:12px 24px 0;border-bottom:1px solid rgba(255,255,255,0.06);}
    .tab{padding:7px 18px;font-size:12px;font-weight:400;cursor:pointer;background:transparent;color:rgba(255,255,255,0.3);border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;transition:all .15s;}
    .tab.active{background:rgba(255,255,255,0.04);color:#fff;border-color:rgba(255,255,255,0.08);}
    .tab-content{display:none;padding:16px 24px;}
    .tab-content.active{display:block;}
    .container{max-width:1280px;margin:0 auto;}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-bottom:14px;}
    .mc{background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;border:1px solid rgba(255,255,255,0.07);}
    .mc-lbl{font-size:10px;color:rgba(255,255,255,0.3);margin-bottom:6px;letter-spacing:0.06em;text-transform:uppercase;}
    .mc-val{font-size:20px;font-weight:500;color:#fff;letter-spacing:-0.5px;}
    .mc-sub{font-size:11px;margin-top:4px;}
    .pos{color:#34d399;}.neg{color:#f87171;}.neu{color:rgba(255,255,255,0.3);}
    .chart-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;overflow:hidden;margin-bottom:12px;}
    .chart-header{padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.06);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;}
    .chart-title{font-size:12px;font-weight:500;color:rgba(255,255,255,0.7);margin-bottom:5px;}
    .legend{display:flex;gap:12px;font-size:11px;color:rgba(255,255,255,0.35);flex-wrap:wrap;}
    .legend span{display:flex;align-items:center;gap:5px;}
    .src-link{font-size:10px;color:#60a5fa;text-decoration:none;opacity:0.7;margin-left:8px;}
    .src-link:hover{opacity:1;}
    .zoom-btns{display:flex;gap:4px;}
    .zoom-btns button{font-size:11px;padding:3px 10px;border:1px solid rgba(255,255,255,0.1);border-radius:6px;background:transparent;cursor:pointer;color:rgba(255,255,255,0.4);}
    .zoom-btns button:hover{background:rgba(255,255,255,0.06);color:#fff;}
    .section-title{font-size:10px;font-weight:500;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px;padding-left:2px;display:flex;justify-content:space-between;align-items:center;}
    .method-box{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-left:3px solid rgba(96,165,250,0.4);border-radius:0 10px 10px 0;padding:16px 18px;margin-bottom:12px;font-size:12px;line-height:1.7;}
    .method-box h3{font-size:11px;font-weight:500;color:#60a5fa;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;}
    .method-box .formula{font-family:'SF Mono','Courier New',monospace;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);padding:8px 12px;border-radius:6px;margin:6px 0;font-size:12px;color:rgba(255,255,255,0.7);}
    .method-box .desc{color:rgba(255,255,255,0.45);margin:4px 0;}
    .method-box .warn{color:rgba(255,255,255,0.25);font-size:11px;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.06);}
    .model-info{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:8px 12px;margin-top:8px;font-family:'SF Mono','Courier New',monospace;font-size:11px;color:rgba(255,255,255,0.35);}
    .tbl-wrap{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;overflow-x:auto;margin-bottom:12px;}
    table{width:100%;border-collapse:collapse;font-size:12px;}
    thead tr{background:rgba(255,255,255,0.04);}
    thead th{padding:9px 12px;text-align:right;font-weight:500;font-size:10px;white-space:nowrap;color:rgba(255,255,255,0.3);letter-spacing:0.05em;text-transform:uppercase;border-bottom:1px solid rgba(255,255,255,0.06);}
    thead th:first-child,thead th:nth-child(2){text-align:left;}
    tbody tr:hover{background:rgba(255,255,255,0.02);}
    tbody td{padding:7px 12px;text-align:right;border-bottom:1px solid rgba(255,255,255,0.04);white-space:nowrap;color:rgba(255,255,255,0.5);}
    tbody td:first-child,tbody td:nth-child(2){text-align:left;color:rgba(255,255,255,0.4);}
    .badge-up{background:rgba(52,211,153,0.1);color:#34d399;padding:2px 6px;border-radius:4px;font-size:11px;}
    .badge-dn{background:rgba(248,113,113,0.1);color:#f87171;padding:2px 6px;border-radius:4px;font-size:11px;}
    .summary-box{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px 18px;margin-bottom:12px;font-size:12px;}
    .summary-box .row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);}
    .summary-box .row:last-child{border-bottom:none;}
    .summary-box .lbl{color:rgba(255,255,255,0.35);}
    .summary-box .val{font-weight:500;color:#fff;}
    .divider{border:none;border-top:1px solid rgba(255,255,255,0.06);margin:4px 0 10px;}
    .bar-cell{display:flex;align-items:center;gap:6px;justify-content:flex-end;}
    .bar{height:6px;border-radius:3px;display:inline-block;opacity:0.7;}
    .info-box{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-left:3px solid rgba(96,165,250,0.3);border-radius:0 10px 10px 0;padding:12px 16px;font-size:12px;line-height:1.7;color:rgba(255,255,255,0.4);margin-bottom:12px;}
    .error{background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.2);border-radius:10px;padding:14px;color:#f87171;margin-bottom:12px;font-size:13px;}
    .loading{text-align:center;padding:60px;color:rgba(255,255,255,0.25);font-size:14px;}
    .footer{font-size:10px;color:rgba(255,255,255,0.15);text-align:center;padding:12px;border-top:1px solid rgba(255,255,255,0.05);margin-top:4px;}
    /* DTS ?뱀뀡 */
    .dts-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;}
    .dts-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px 16px;}
    .dts-hd{font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;display:flex;align-items:center;gap:6px;}
    .dts-dot{width:6px;height:6px;border-radius:50%;display:inline-block;}
    .dts-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);}
    .dts-row:last-child{border-bottom:none;}
    .dts-name{font-size:12px;color:rgba(255,255,255,0.4);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:10px;}
    .dts-amt{font-size:12px;font-weight:500;white-space:nowrap;}
    .c-in{color:#34d399;}.c-out{color:#f87171;}
    /* 罹섎┛??*/
    .cal-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px;}
    .cal-m{background:rgba(255,255,255,0.025);border-radius:8px;padding:9px 10px;border:1px solid rgba(255,255,255,0.05);}
    .cal-m.hl-red{border-color:rgba(248,113,113,0.3);}
    .cal-m.hl-green{border-color:rgba(52,211,153,0.2);}
    .cal-mn{font-size:10px;color:rgba(255,255,255,0.3);margin-bottom:6px;font-weight:500;}
    .cal-mn.red{color:#f87171;}.cal-mn.green{color:#34d399;}
    .cal-ev{font-size:10px;padding:2px 6px;border-radius:4px;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;}
    .ev-out{background:rgba(248,113,113,0.1);color:#f87171;}
    .ev-in{background:rgba(52,211,153,0.1);color:#34d399;}
    .ev-neu{background:rgba(255,255,255,0.05);color:rgba(255,255,255,0.28);}
    .cal-legend{display:flex;gap:14px;margin-bottom:10px;font-size:11px;color:rgba(255,255,255,0.35);}
    .cal-legend span{display:flex;align-items:center;gap:5px;}
    .cal-legend-dot{width:8px;height:8px;border-radius:50%;display:inline-block;}
    /* QRA 寃쎈ℓ ?댄똻 - JS ?쒖뼱 fixed ?앹뾽 */
    #auction-tooltip{display:none;position:fixed;z-index:9999;
      background:#1a1a22;border:1px solid rgba(255,255,255,0.15);border-radius:8px;
      padding:10px 13px;width:230px;pointer-events:none;
      font-size:11px;line-height:1.55;color:rgba(255,255,255,0.45);}
    #auction-tooltip b{color:rgba(255,255,255,0.8);font-weight:500;display:block;margin-bottom:4px;}
    #auction-tooltip .tip-liq{margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.08);font-size:11px;}
    #auction-tooltip .tip-neg{color:#f87171;font-weight:500;}
    #auction-tooltip .tip-neu{color:rgba(255,255,255,0.45);font-weight:500;}
    .has-tip{cursor:default;}
    /* ?몃씪????(DTS/QRA) */
    .itab-row{display:flex;gap:4px;margin-bottom:10px;}
    .itab{font-size:11px;padding:4px 14px;border:1px solid rgba(255,255,255,0.1);border-radius:20px;background:transparent;cursor:pointer;color:rgba(255,255,255,0.3);transition:all .15s;}
    .itab.active{background:rgba(96,165,250,0.12);border-color:rgba(96,165,250,0.35);color:#60a5fa;}
    .itab-panel{display:none;}.itab-panel.active{display:block;}
    /* QRA 諛?李⑦듃 */
    .qra-bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:11px;}
    .qra-bar-label{width:110px;color:rgba(255,255,255,0.4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0;}
    .qra-bar-bg{flex:1;height:5px;background:rgba(255,255,255,0.05);border-radius:3px;}
    .qra-bar-fill{height:5px;border-radius:3px;transition:width .4s;}
    .qra-bar-amt{width:64px;text-align:right;color:rgba(255,255,255,0.5);font-weight:500;flex-shrink:0;}
    .qra-tag{font-size:10px;padding:1px 7px;border-radius:4px;margin-left:6px;flex-shrink:0;}
    .tag-out{background:rgba(248,113,113,0.1);color:#f87171;}
    .tag-in{background:rgba(52,211,153,0.1);color:#34d399;}
    .tag-neu{background:rgba(255,255,255,0.05);color:rgba(255,255,255,0.3);}
    .qra-pill-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
    .qra-pill{font-size:10px;padding:2px 10px;border-radius:20px;border:1px solid rgba(255,255,255,0.1);color:rgba(255,255,255,0.3);}
    .qra-pill.hl{border-color:rgba(96,165,250,0.4);color:#60a5fa;background:rgba(96,165,250,0.08);}
    /* ?묎린/?쇱튂湲?*/
    details.collapsible{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;margin-bottom:12px;overflow:hidden;}
    details.collapsible summary{padding:11px 16px;font-size:10px;font-weight:500;color:rgba(255,255,255,0.35);text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;display:flex;align-items:center;gap:8px;list-style:none;user-select:none;}
    details.collapsible summary::-webkit-details-marker{display:none;}
    details.collapsible summary::before{content:'??;font-size:8px;color:rgba(255,255,255,0.2);transition:transform .2s;flex-shrink:0;}
    details.collapsible[open] summary::before{transform:rotate(90deg);}
    details.collapsible summary:hover{color:rgba(255,255,255,0.6);background:rgba(255,255,255,0.02);}
    .collapsible-body{padding:14px 16px;border-top:1px solid rgba(255,255,255,0.06);}
  </style>
  <script>
    window.onload=function(){
      {% if not summary and not error %}setTimeout(()=>location.reload(),10000);{% endif %}
    };
    function manualRefresh(){
      document.getElementById('cd').textContent='媛깆떊 以?..';
      fetch('/refresh').then(()=>setTimeout(()=>location.reload(),3000));
    }
    function switchTab(id){
      document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
      document.getElementById('tab-btn-'+id).classList.add('active');
      document.getElementById('tab-'+id).classList.add('active');
    }
    function switchItab(sec, id){
      document.querySelectorAll('#'+sec+' .itab').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('#'+sec+' .itab-panel').forEach(p=>p.classList.remove('active'));
      document.getElementById(sec+'-tab-'+id).classList.add('active');
      document.getElementById(sec+'-panel-'+id).classList.add('active');
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
    // 寃쎈ℓ ?댄똻 (留덉슦???꾩튂 湲곕컲 fixed)
    document.addEventListener('DOMContentLoaded', function(){
      const tip = document.createElement('div');
      tip.id = 'auction-tooltip';
      document.body.appendChild(tip);
      document.addEventListener('mouseover', function(e){
        const el = e.target.closest('.has-tip');
        if(!el) return;
        const title = el.dataset.tipTitle || '';
        const body  = el.dataset.tipBody  || '';
        const liq   = el.dataset.tipLiq   || '';
        const neg   = el.dataset.tipNeg === 'true';
        tip.innerHTML =
          '<b>' + title + '</b>' +
          body +
          (liq ? '<div class="tip-liq">NL: <span class="' + (neg?'tip-neg':'tip-neu') + '">' + liq + '</span></div>' : '');
        tip.style.display = 'block';
      });
      document.addEventListener('mousemove', function(e){
        if(!tip.style.display || tip.style.display==='none') return;
        const vw = window.innerWidth;
        const tw = 240;
        let x = e.clientX + 12;
        if(x + tw > vw) x = e.clientX - tw - 8;
        tip.style.left = x + 'px';
        tip.style.top  = (e.clientY + 14) + 'px';
      });
      document.addEventListener('mouseout', function(e){
        if(!e.target.closest('.has-tip')) return;
        tip.style.display = 'none';
      });
    });
  </script>
</head>
<body>
<div class="header">
  <h1><span class="nav-dot"></span> Fed Dashboard <span class="badge">??Live</span></h1>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <span class="meta" id="cd">Updated: {{ updated_at }}</span>
    <button class="refresh-btn" onclick="manualRefresh()">??Refresh</button>
  </div>
</div>

<div class="container">
<div class="tabs">
  <div class="tab active" id="tab-btn-nl" onclick="switchTab('nl')">Net Liquidity</div>
  <div class="tab" id="tab-btn-tic" onclick="switchTab('tic')">援??蹂?誘멸뎅梨?蹂댁쑀</div>
</div>

<div id="tab-nl" class="tab-content active">
{% if error %}
  <div class="error">Error: {{ error }}</div>
{% elif not summary %}
  <div class="loading">FRED ?곗씠??濡쒕뵫 以?.. ?좎떆 ???먮룞 ?덈줈怨좎묠?⑸땲??</div>
{% else %}

  <div class="metrics">
    <div class="mc"><div class="mc-lbl">Net Liquidity</div><div class="mc-val">{{ summary.nl }}</div><div class="mc-sub {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_chg }}</div></div>
    <div class="mc"><div class="mc-lbl">NL Regression FV</div><div class="mc-val">{{ summary.fv_nl }}</div><div class="mc-sub {{ 'pos' if summary.fv_nl_cheap else ('neg' if summary.fv_nl_cheap is not none else 'neu') }}">{{ summary.fv_nl_gap }}</div></div>
    <div class="mc"><div class="mc-lbl">WALCL <span style="font-weight:400;color:#bbb;">二쇨컙</span> <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.walcl }}</div><div class="mc-sub neu">{{ summary.walcl_date }} 쨌 H.4.1 留ㅼ＜ ?섏슂??/div></div>
    <div class="mc"><div class="mc-lbl">TGA <span style="font-weight:400;color:#bbb;">二쇨컙</span> <a class="src-link" href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.tga }}</div><div class="mc-sub neu">{{ summary.tga_date }} 쨌 ?ㅼ쓬 諛쒗몴 ~{{ next_h41 }}</div></div>
    <div class="mc"><div class="mc-lbl">RRP <span style="font-weight:400;color:#bbb;">?쇨컙</span> <a class="src-link" href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.rrp }}</div><div class="mc-sub neu">{{ summary.rrp_date }}</div></div>
    <div class="mc"><div class="mc-lbl">S&P 500</div><div class="mc-val">{{ summary.spx_raw }}</div><div class="mc-sub neu">{{ summary.base_date }}</div></div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">WALCL 援ъ꽦: Net Liquidity 쨌 TGA 쨌 RRP ??Daily (2000?뱎resent)
        <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED ??/a>
      </div>
      <div class="legend">
        <span><span style="width:12px;height:8px;background:rgba(96,165,250,0.6);border-radius:2px;display:inline-block;"></span>Net Liquidity</span>
        <span><span style="width:12px;height:8px;background:rgba(52,211,153,0.55);border-radius:2px;display:inline-block;"></span>TGA</span>
        <span><span style="width:12px;height:8px;background:rgba(251,191,36,0.55);border-radius:2px;display:inline-block;"></span>RRP</span>
        <span style="font-size:10px;color:rgba(255,255,255,0.2);">?뚯쁺: 寃쎄린移⑥껜</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c1','in')">+</button><button onclick="zoomChart('c1','out')">??/button><button onclick="resetChart('c1')">??/button></div>
    </div>
    <div id="c1" style="padding:4px;">{{ chart1_html | safe }}</div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">S&P 500 vs NL Regression FV ??Daily (2000?뱎resent)
        <a class="src-link" href="https://fred.stlouisfed.org/series/SP500" target="_blank">FRED ??/a>
      </div>
      <div class="legend">
        <span><span style="width:16px;height:2px;background:#e2e2e2;display:inline-block;"></span>S&P 500</span>
        <span><span style="width:16px;height:2px;border-top:2px dashed #60a5fa;display:inline-block;"></span>NL ?뚭? FV</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c2','in')">+</button><button onclick="zoomChart('c2','out')">??/button><button onclick="resetChart('c2')">??/button></div>
    </div>
    <div id="c2" style="padding:4px;">{{ chart2_html | safe }}</div>
  </div>

  <div class="section-title">TGA ?ъ슜泥?쨌 DTS 쨌 QRA
    <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ dts_date }} 湲곗?</span>
    <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank">fiscaldata ??/a>
  </div>

  <div id="dts-qra-tabs">
    <div class="itab-row">
      <button class="itab active" id="dts-qra-tabs-tab-dts" onclick="switchItab('dts-qra-tabs','dts')">DTS ?쇱씪 ?댁뿭</button>
      <button class="itab" id="dts-qra-tabs-tab-qra" onclick="switchItab('dts-qra-tabs','qra')">QRA 援?콈諛쒗뻾</button>
    </div>

    <!-- DTS ?⑤꼸 -->
    <div class="itab-panel active" id="dts-qra-tabs-panel-dts">
      {% if dts_error %}
      <div class="error" style="font-size:12px;">DTS ?곗씠???ㅻ쪟: {{ dts_error }}</div>
      {% elif not dts_deposits %}
      <div class="loading" style="padding:20px;">DTS ?곗씠??濡쒕뵫 以?..</div>
      {% else %}
      <div class="dts-grid">
        <div class="dts-card">
          <div class="dts-hd"><span class="dts-dot" style="background:#34d399;"></span>二쇱슂 ?낃툑 ??ぉ (Table II)
            <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/deposits-withdrawals-operating-cash" target="_blank">??/a>
          </div>
          {% for item in dts_deposits %}
          <div class="dts-row">
            <span class="dts-name">{{ item.name }}</span>
            <span class="dts-amt c-in">+{{ item.amt }}</span>
          </div>
          {% endfor %}
        </div>
        <div class="dts-card">
          <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>二쇱슂 異쒓툑 ??ぉ (Table II)
            <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/deposits-withdrawals-operating-cash" target="_blank">??/a>
          </div>
          {% for item in dts_withdrawals %}
          <div class="dts-row">
            <span class="dts-name">{{ item.name }}</span>
            <span class="dts-amt c-out">-{{ item.amt }}</span>
          </div>
          {% endfor %}
        </div>
      </div>
      <div class="dts-card" style="margin-bottom:12px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>TGA ?뱀씪 ?쒕????붿빟
          <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/operating-cash-balance" target="_blank">??/a>
        </div>
        {% for item in dts_balance %}
        <div class="dts-row">
          <span class="dts-name">{{ item.name }}</span>
          <span class="dts-amt" style="color:{{ '#34d399' if item.pos else '#f87171' }};">{{ item.amt }}</span>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </div>

    <!-- QRA ?⑤꼸 -->
    <div class="itab-panel" id="dts-qra-tabs-panel-qra">
      {% if qra_error %}
      <div class="error" style="font-size:12px;">QRA ?곗씠???ㅻ쪟: {{ qra_error }}</div>
      {% elif not qra_data %}
      <div class="loading" style="padding:20px;">QRA ?곗씠??濡쒕뵫 以?..</div>
      {% else %}
      <!-- 硫뷀듃由?移대뱶 -->
      <div class="metrics" style="margin-bottom:10px;">
        <div class="mc"><div class="mc-lbl">?ㅼ쓬 QRA 諛쒗몴</div><div class="mc-val" style="font-size:16px;">{{ qra_data.next_qra }}</div><div class="mc-sub neu">遺꾧린 李⑥엯 ?섏슂 諛쒗몴</div></div>
        <div class="mc"><div class="mc-lbl">理쒓렐 T-Bill 諛쒗뻾 (30??</div><div class="mc-val">{{ qra_data.tbill_30d }}</div><div class="mc-sub neg">?좊룞???≪닔??/div></div>
        <div class="mc"><div class="mc-lbl">理쒓렐 荑좏룿梨?諛쒗뻾 (30??</div><div class="mc-val">{{ qra_data.coupon_30d }}</div><div class="mc-sub neg">NL ?뺣컯??/div></div>
        <div class="mc"><div class="mc-lbl">理쒓렐 TIPS 諛쒗뻾 (30??</div><div class="mc-val">{{ qra_data.tips_30d }}</div><div class="mc-sub neu">臾쇨??곕룞</div></div>
        <div class="mc"><div class="mc-lbl">?됯퇏 ?묒같瑜?(BTC)</div><div class="mc-val">{{ qra_data.avg_btc }}</div><div class="mc-sub neu">理쒓렐 30???됯퇏</div></div>
        <div class="mc"><div class="mc-lbl">珥?諛쒗뻾 (30??</div><div class="mc-val">{{ qra_data.total_30d }}</div><div class="mc-sub neg">?쒖옣 ?≪닔 洹쒕え??/div></div>
      </div>

      <!-- 諛쒗뻾 援ъ꽦 諛?-->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>援?콈 諛쒗뻾 援ъ꽦 (理쒓렐 30??쨌 ?좊룞???≪닔)
          <a class="src-link" href="https://www.treasurydirect.gov/TA_WS/securities/auctioned" target="_blank">TreasuryDirect ??/a>
        </div>
        {% for item in qra_data.breakdown %}
        <div class="qra-bar-row">
          <span class="qra-bar-label">{{ item.label }}</span>
          <div class="qra-bar-bg"><div class="qra-bar-fill" style="width:{{ item.pct }}%;background:{{ item.color }};"></div></div>
          <span class="qra-bar-amt">{{ item.amt }}</span>
        </div>
        {% endfor %}
        <div style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:8px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);">
          * 援?콈 諛쒗뻾 ??TGA ?좎엯 ??NL 媛먯냼. T-Bill ?꾩＜ 諛쒗뻾 ??MMF(RRP)???곸뇙 ?④낵 ?덉쓬.
        </div>
      </div>

      <!-- QRA ?먮룆 湲곗? -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>QRA ?좊룞???먮룆 湲곗?</div>
        <div class="dts-row"><span class="dts-name">T-Bill 鍮꾩쨷 ?믪쓬</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">RRP???곸뇙</span><span class="qra-tag tag-in">NL 以묐┰~?좎엯</span></div>
        <div class="dts-row"><span class="dts-name">荑좏룿梨?鍮꾩쨷 ?믪쓬</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">???以鍮꾧툑 ?≪닔</span><span class="qra-tag tag-out">NL ?뺣컯</span></div>
        <div class="dts-row"><span class="dts-name">李⑥엯 洹쒕え ?덉긽??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA 湲됱쬆 ?덇퀬</span><span class="qra-tag tag-out">NL ?섎씫 ?좏샇</span></div>
        <div class="dts-row"><span class="dts-name">李⑥엯 洹쒕え ?덉긽??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?꾨쭔 ?좎?</span><span class="qra-tag tag-in">NL ?덉젙 ?좏샇</span></div>
        <div class="dts-row"><span class="dts-name">遺梨꾪븳???묒긽 以?/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?뚯쭊 吏??/span><span class="qra-tag tag-in">NL ?몄쐞???곸듅</span></div>
        <div class="dts-row"><span class="dts-name">遺梨꾪븳???댁냼 ??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?ъ땐??/span><span class="qra-tag tag-out">NL 湲됰씫 ?꾪뿕</span></div>
      </div>

      <!-- 諛쒗몴 ?쇱젙 -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#fbbf24;"></span>QRA 諛쒗몴 ?쇱젙 (2026)</div>
        <div class="qra-pill-row">
          {% for q in qra_data.schedule %}
          <span class="qra-pill {{ 'hl' if q.current else '' }}">{{ q.label }}</span>
          {% endfor %}
        </div>
        <div style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:8px;">
          TBAC 諛쒗몴 ?뱀씪 ?쒖옣 蹂?숈꽦 二쇱쓽. 李⑥엯 洹쒕え????湲덈━??쨌 NL???뺣젰.
        </div>
      </div>

      <!-- 理쒓렐 寃쎈ℓ ?댁뿭 -->
      <div class="section-title" style="margin-top:4px;">理쒓렐 寃쎈ℓ ?댁뿭 (30??
        <a class="src-link" href="https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&dateFieldName=auctionDate&startDate={{ qra_data.start_date }}" target="_blank">?먮낯 ??/a>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th style="text-align:left;">寃쎈ℓ??/th>
            <th style="text-align:left;">醫낅쪟</th>
            <th style="text-align:left;">留뚭린</th>
            <th>諛쒗뻾??B)</th>
            <th>?묒같瑜?/th>
            <th>湲덈━/?좎씤??/th>
          </tr></thead>
          <tbody>
            {% for r in qra_data.auctions %}
            <tr>
              <td style="text-align:left;">{{ r.date }}</td>
              <td style="text-align:left;">
                <span class="has-tip" style="font-size:11px;padding:1px 7px;border-radius:4px;background:{{ r.type_bg }};color:{{ r.type_color }};"
                  data-tip-title="{{ r.tip_title }} 쨌 {{ r.term }}"
                  data-tip-body="{{ r.tip_body }}"
                  data-tip-liq="{{ r.tip_liq }}"
                  data-tip-neg="{{ 'true' if r.tip_neg else 'false' }}">{{ r.stype }}</span>
              </td>
              <td style="text-align:left;color:rgba(255,255,255,0.4);">{{ r.term }}</td>
              <td>{{ r.amt }}</td>
              <td>
                <span class="{{ 'badge-up' if r.btc_ok else 'badge-dn' }} has-tip"
                  data-tip-title="?묒같瑜?(Bid-to-Cover)"
                  data-tip-body="寃쎌웳 ?낆같 ?쒖텧??첨 ?숈같?? ?섏슂 媛뺣룄 吏??"
                  data-tip-liq="{{ '2.3x???섏슂 ?묓샇' if r.btc_ok else '2.3x???섏슂 遺議?寃쎄퀬' }}"
                  data-tip-neg="{{ 'false' if r.btc_ok else 'true' }}">{{ r.btc }}</span>
              </td>
              <td>
                <span class="has-tip" style="color:rgba(255,255,255,0.5);"
                  data-tip-title="?숈같 湲덈━/?좎씤??
                  data-tip-body="{{ 'T-Bill: ?좎씤??Discount Rate) 湲곗?. ?믪쓣?섎줉 ?④린 ?먭툑 鍮꾩슜??' if r.is_bill else '荑좏룿梨? 理쒓퀬 ?숈같 ?섏씡瑜?High Yield). ?믪쓣?섎줉 ?ъ젙 ?댁옄 遺?닳넁 쨌 NL ?κ린 ?뺣컯.' }}"
                  data-tip-liq="{{ '?④린湲덈━ 諛⑺뼢??吏?? if r.is_bill else '?κ린湲덈━????二쇱떇 硫?고뵆 ?뺣컯' }}"
                  data-tip-neg="{{ 'false' if r.is_bill else 'true' }}">{{ r.rate }}</span>
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% endif %}
    </div>
  </div>

  <div class="section-title">?ъ젙 ?대깽??罹섎┛??
    <a class="src-link" href="https://www.irs.gov/businesses/small-businesses-self-employed/tax-calendar" target="_blank">IRS Calendar ??/a>
  </div>
  <div class="chart-card" style="padding:14px 16px;margin-bottom:12px;">
    <div class="cal-legend">
      <span><span class="cal-legend-dot" style="background:#34d399;"></span>?좊룞???좎엯 (?섍툒쨌?뺣?吏異?</span>
      <span><span class="cal-legend-dot" style="background:#f87171;"></span>?좊룞???좎텧 (?멸툑?⑸?쨌援?콈諛쒗뻾)</span>
      <span><span class="cal-legend-dot" style="background:rgba(255,255,255,0.2);"></span>以묐┰/諛쒗몴</span>
    </div>
    <div class="cal-grid">
      <div class="cal-m"><div class="cal-mn">1??/div>
        <span class="cal-ev ev-out">4Q 異붿젙???⑸? (1/15)</span>
        <span class="cal-ev ev-neu">IRS ?좉퀬?쒖쫵 媛쒖떆</span>
        <span class="cal-ev ev-in">?ы쉶蹂댁옣쨌硫붾뵒耳?닳넁</span>
        <span class="cal-ev ev-neu">QRA 諛쒗몴(~1/29)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">2??/div>
        <span class="cal-ev ev-in">?섍툒 ?쇳겕 (W-2)?묅넁</span>
        <span class="cal-ev ev-in">EITC쨌CTC ?섍툒 媛쒖떆</span>
        <span class="cal-ev ev-in">?ы쉶蹂댁옣쨌硫붾뵒耳?닳넁</span>
        <span class="cal-ev ev-neu">H.4.1 留ㅼ＜ ?섏슂??/span>
      </div>
      <div class="cal-m"><div class="cal-mn">3??/div>
        <span class="cal-ev ev-in">?섍툒 吏?띯넁</span>
        <span class="cal-ev ev-neu">S-Corp쨌?뚰듃?덉떗 ?좉퀬(3/15)</span>
        <span class="cal-ev ev-neu">T-Note 遺꾧린諛쒗뻾</span>
        <span class="cal-ev ev-out">援?콈 留뚭린쨌濡ㅼ삤踰꾟넃</span>
      </div>
      <div class="cal-m hl-red"><div class="cal-mn red">4????/div>
        <span class="cal-ev ev-out">Tax Day (4/15)?볛넃</span>
        <span class="cal-ev ev-out">1Q 異붿젙??(4/15)??/span>
        <span class="cal-ev ev-out">TGA 湲됱쬆 ??NL 媛먯냼</span>
        <span class="cal-ev ev-neu">?곗옣?좎껌(Form 4868)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">5??/div>
        <span class="cal-ev ev-in">?붿뿬 ?섍툒 吏?띯넁</span>
        <span class="cal-ev ev-neu">Form 990 鍮꾩쁺由??좉퀬</span>
        <span class="cal-ev ev-in">?뺣? 吏異??뺤긽?붴넁</span>
        <span class="cal-ev ev-neu">QRA 諛쒗몴(~4?붾쭚)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">6??/div>
        <span class="cal-ev ev-out">2Q 異붿젙??(6/15)??/span>
        <span class="cal-ev ev-in">援?갑쨌?명봽??吏異쒋넁</span>
        <span class="cal-ev ev-neu">T-Bill ?뺢린 濡ㅼ삤踰?/span>
        <span class="cal-ev ev-neu">FOMC ?뚯쓽(?듭긽)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">7??/div>
        <span class="cal-ev ev-in">?ы쉶蹂댁옣 吏湲됤넁</span>
        <span class="cal-ev ev-in">硫붾뵒耳?는룸찓?붿??대뱶??/span>
        <span class="cal-ev ev-in">?щ쫫 ?명봽??吏異쒋넁</span>
        <span class="cal-ev ev-neu">QRA 諛쒗몴(~7/28)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">8??/div>
        <span class="cal-ev ev-out">T-Bill ?洹쒕え 諛쒗뻾??/span>
        <span class="cal-ev ev-neu">QRA쨌TBAC 諛쒗몴</span>
        <span class="cal-ev ev-in">?뺣? ?щ웾吏異쒋넁</span>
        <span class="cal-ev ev-neu">??뒯? ?곗꽕(?곗?)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">9??/div>
        <span class="cal-ev ev-out">3Q 異붿젙??(9/15)??/span>
        <span class="cal-ev ev-in">?뚭퀎?곕룄 留덇컧 吏異쒋넁??/span>
        <span class="cal-ev ev-out">援?콈 遺꾧린 諛쒗뻾??/span>
        <span class="cal-ev ev-neu">?뚭퀎?곕룄 醫낅즺(9/30)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">10??/div>
        <span class="cal-ev ev-neu">???뚭퀎?곕룄 媛쒖떆(FY)</span>
        <span class="cal-ev ev-neu">?곗옣 留덇컧(10/15)</span>
        <span class="cal-ev ev-in">?ы쉶蹂댁옣 COLA ?몄긽??/span>
        <span class="cal-ev ev-neu">TIC ?곗씠??諛쒗몴(~18??</span>
      </div>
      <div class="cal-m"><div class="cal-mn">11??/div>
        <span class="cal-ev ev-in">?곕쭚 ?뺣? 吏異쒋넁</span>
        <span class="cal-ev ev-in">?ы쉶蹂댁옣쨌蹂듭?吏異쒋넁</span>
        <span class="cal-ev ev-neu">QRA 諛쒗몴(~10?붾쭚)</span>
        <span class="cal-ev ev-neu">T-Bond 遺꾧린諛쒗뻾</span>
      </div>
      <div class="cal-m hl-green"><div class="cal-mn green">12????/div>
        <span class="cal-ev ev-in">吏異??쇳겕?묅넁 (?뚭퀎留덇컧)</span>
        <span class="cal-ev ev-out">?곕쭚 ?멸툑 ?⑸???/span>
        <span class="cal-ev ev-in">?ы쉶蹂댁옣 ?좎?湲됤넁</span>
        <span class="cal-ev ev-neu">?곗? 理쒖쥌 FOMC</span>
      </div>
    </div>
  </div>

  <details class="collapsible">
    <summary>?쒖옣 ?좊룞??湲곗? <a class="src-link" href="https://www.federalreserve.gov/releases/h41/" target="_blank" onclick="event.stopPropagation()">H.4.1 ??/a></summary>
    <div class="collapsible-body">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:12px;line-height:1.8;">
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">?뱿 ?좊룞???좎엯 ?좏샇 (NL ?곸듅 議곌굔)</div>
          <div class="dts-row"><span class="dts-name">WALCL 利앷?</span><span style="color:#34d399;font-size:11px;">Fed ?먯궛 留ㅼ엯 ???쒖쨷 ?먭툑??/span></div>
          <div class="dts-row"><span class="dts-name">TGA 媛먯냼</span><span style="color:#34d399;font-size:11px;">?щТ遺 吏異??????以鍮꾧툑??/span></div>
          <div class="dts-row"><span class="dts-name">RRP 媛먯냼</span><span style="color:#34d399;font-size:11px;">MMF ?먭툑 ?쒖옣 ?좎엯??/span></div>
          <div class="dts-row"><span class="dts-name">遺梨꾪븳???묒긽</span><span style="color:#34d399;font-size:11px;">TGA ?뚯쭊 ??NL 湲됱긽??/span></div>
          <div class="dts-row"><span class="dts-name">QE ?ш컻</span><span style="color:#34d399;font-size:11px;">WALCL ?뺣? ??吏곸젒 ?좊룞?기넁</span></div>
          <div class="dts-row"><span class="dts-name">?섍툒 ?쒖쫵 (2~3??</span><span style="color:#34d399;font-size:11px;">TGA 媛먯냼쨌?뚮퉬??/span></div>
          <div class="dts-row"><span class="dts-name">SRF쨌?뺤콉 ?異?/span><span style="color:#34d399;font-size:11px;">Fed 湲닿툒 ?좊룞??怨듦툒??/span></div>
          <div class="dts-row"><span class="dts-name">?명솚蹂댁쑀 ?щ윭 ?섎쪟</span><span style="color:#34d399;font-size:11px;">?댁쇅 以묒븰????ㅼ솑?쇱씤??/span></div>
        </div>
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">?뱾 ?좊룞???좎텧 ?좏샇 (NL ?섎씫 議곌굔)</div>
          <div class="dts-row"><span class="dts-name">WALCL 媛먯냼 (QT)</span><span style="color:#f87171;font-size:11px;">Fed ?먯궛 異뺤냼 ??以鍮꾧툑 媛먯냼??/span></div>
          <div class="dts-row"><span class="dts-name">TGA 湲됱쬆</span><span style="color:#f87171;font-size:11px;">?멸툑?⑸?쨌援?콈諛쒗뻾 ???쒖쨷 ?≪닔??/span></div>
          <div class="dts-row"><span class="dts-name">RRP 利앷?</span><span style="color:#f87171;font-size:11px;">MMF媛 Fed???먭툑 ?덉튂??/span></div>
          <div class="dts-row"><span class="dts-name">Tax Day (4??</span><span style="color:#f87171;font-size:11px;">TGA 湲됱쬆 ??NL ?④린 ?뺣컯??/span></div>
          <div class="dts-row"><span class="dts-name">異붿젙???⑸?(遺꾧린)</span><span style="color:#f87171;font-size:11px;">1/15 쨌 4/15 쨌 6/15 쨌 9/15??/span></div>
          <div class="dts-row"><span class="dts-name">T-Bill ?洹쒕え 諛쒗뻾</span><span style="color:#f87171;font-size:11px;">?쒖쨷 ?먭툑 援?콈濡??≪닔??/span></div>
          <div class="dts-row"><span class="dts-name">遺梨꾪븳???댁냼 ??/span><span style="color:#f87171;font-size:11px;">TGA ?ъ땐????NL 湲됰씫??/span></div>
          <div class="dts-row"><span class="dts-name">湲곗?湲덈━ ?몄긽</span><span style="color:#f87171;font-size:11px;">RRP 湲덈━ 留ㅻ젰?????먭툑?좎텧??/span></div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:rgba(255,255,255,0.25);">
        ?뮕 <b style="color:rgba(255,255,255,0.4);">?듭떖 怨듭떇:</b> NL = WALCL ??TGA ??RRP &nbsp;쨌&nbsp;
        NL???곸듅?섎㈃ ?쒖쨷 ?좊룞??利앷? ???꾪뿕?먯궛 ?좏샇 寃쏀뼢 &nbsp;쨌&nbsp;
        <a href="https://fred.stlouisfed.org/series/WALCL" target="_blank" style="color:#60a5fa;text-decoration:none;">WALCL??/a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank" style="color:#60a5fa;text-decoration:none;">TGA??/a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank" style="color:#60a5fa;text-decoration:none;">RRP??/a>
      </div>
    </div>
  </details>

  <details class="collapsible">
    <summary>怨꾩궛 諛⑸쾿濡?/summary>
    <div class="collapsible-body">
      <div class="method-box" style="margin-bottom:0;">
        <h3>1. Net Liquidity</h3>
        <div class="formula">NL = WALCL ??TGA ??RRP</div>
        <div class="desc"><b>WALCL</b>: Fed 珥앹옄????留롮쓣?섎줉 ?쒖쨷???덉씠 留롮씠 ?由??곹깭</div>
        <div class="desc"><b>TGA 李④컧</b>: ?щТ遺媛 Fed???덉튂???꾧툑 ???쒖옣???由ъ? ?딆? ??/div>
        <div class="desc"><b>RRP 李④컧</b>: MMF ?깆씠 Fed??留↔릿 ??젅???붿븸 ???쒖옣 諛뽰뿉 ?덈뒗 ??/div>
        <div class="desc" style="margin-top:6px;">??Michael Howell(CrossBorder Capital), Lyn Alden ?깆씠 ?以묓솕. Fed ?좊룞?깆씠 ?ㅼ젣濡??쒖옣???쇰쭏????ㅼ엳?붿? 痢≪젙.</div>
        <h3 style="margin-top:14px;">2. NL ?뚭? 怨듭젙媛移?/h3>
        <div class="formula">SPX_FV = slope 횞 NL + intercept</div>
        <div class="desc">2000?꾨????꾩옱源뚯? ?쇨컙 ?곗씠?곕줈 ?좏삎?뚭?. NL????SPX 怨듭젙媛移섃넁 愿怨?紐⑤뜽留?</div>
        {% if model_info %}<div class="model-info">slope={{ model_info.slope }} | intercept={{ model_info.intercept }} | R짼={{ model_info.r2 }} | n={{ model_info.n }}</div>{% endif %}
        <h3 style="margin-top:14px;">3. 愿대━??/h3>
        <div class="formula">愿대━??= (SPX?꾩옱媛 ??FV) / FV 횞 100 (%)</div>
        <div class="desc">?묒닔(+): 怨좏룊媛 &nbsp;|&nbsp; ?뚯닔(??: ??됯?</div>
        <div class="warn">??NL?봖PX ?곴?愿怨?R짼??.6~0.8)???쒕낯 湲곌컙???섏〈?섎ŉ, ?멸낵愿怨꾧? ?꾨땶 ?곴?愿怨꾩엯?덈떎. ?덈???FV蹂대떎 <b>諛⑺뼢?굿룰눼由?異붿꽭</b> ?꾩＜濡??쒖슜 沅뚯옣.</div>
      </div>
    </div>
  </details>

  <div class="section-title">?붿빟</div>
  <div class="summary-box">
    <div class="row"><span class="lbl">湲곗???/span><span class="val">{{ summary.base_date }}</span></div>
    <div class="row"><span class="lbl">WALCL ({{ summary.walcl_date }})</span><span class="val">{{ summary.walcl_raw }}</span></div>
    <div class="row"><span class="lbl">TGA ({{ summary.tga_date }})</span><span class="val">{{ summary.tga_raw }}</span></div>
    <div class="row"><span class="lbl">RRP ({{ summary.rrp_date }})</span><span class="val">{{ summary.rrp_raw }}</span></div>
    <div class="row"><span class="lbl">Net Liquidity</span><span class="val {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_raw }} &nbsp;({{ summary.nl_chg }})</span></div>
    <hr class="divider">
    <div class="row"><span class="lbl">NL ?뚭? 怨듭젙媛移?/span><span class="val">{{ summary.fv_nl }}</span></div>
    <div class="row"><span class="lbl">SPX ?꾩옱媛</span><span class="val {{ 'pos' if summary.fv_nl_cheap else 'neg' }}">{{ summary.spx_raw }} &nbsp;({{ summary.fv_nl_gap }})</span></div>
  </div>

  <div class="section-title">理쒓렐 10 ?곸뾽???곗씠??/div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>?좎쭨</th><th style="text-align:right;">WALCL(B)</th><th style="text-align:right;">TGA(B)</th><th style="text-align:right;">RRP(B)</th><th style="text-align:right;">Net Liq(B)</th><th style="text-align:right;">DoD</th><th style="text-align:right;">SP500</th><th style="text-align:right;">NL FV</th><th style="text-align:right;">愿대━??/th></tr></thead>
      <tbody>
        {% for row in table_rows %}
        <tr>
          <td>{{ row.date }}</td><td>{{ row.walcl }}</td><td>{{ row.tga }}</td><td>{{ row.rrp }}</td>
          <td><strong>{{ row.nl }}</strong></td>
          <td>{% if row.dod_pos is not none %}<span class="{{ 'badge-up' if row.dod_pos else 'badge-dn' }}">{{ row.dod }}</span>{% else %}??% endif %}</td>
          <td>{{ row.spx }}</td><td>{{ row.fv_nl }}</td>
          <td>{% if row.gap is not none %}<span class="{{ 'badge-up' if row.gap_pos else 'badge-dn' }}">{{ row.gap }}</span>{% else %}??% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

{% endif %}
</div>

<div id="tab-tic" class="tab-content">
{% if tic_error %}
  <div class="error">TIC ?곗씠???ㅻ쪟: {{ tic_error }}</div>
{% elif not tic_chart_html %}
  <div class="loading">TIC ?곗씠??濡쒕뵫 以?..</div>
{% else %}

  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">二쇱슂援?誘멸뎅梨?蹂댁쑀????Monthly (2000?뱎resent)
          <a class="src-link" href="https://home.treasury.gov/data/treasury-international-capital-tic-system" target="_blank">TIC ??/a>
        </div>
        <div class="legend">
          {% for c in tic_legend %}
          <span><span style="width:16px;height:2px;background:{{ c.color }};display:inline-block;"></span>{{ c.name }}</span>
          {% endfor %}
        </div>
      </div>
      <div class="zoom-btns"><button onclick="zoomChart('ctic','in')">+</button><button onclick="zoomChart('ctic','out')">??/button><button onclick="resetChart('ctic')">??/button></div>
    </div>
    <div id="ctic" style="padding:4px;">{{ tic_chart_html | safe }}</div>
  </div>

  <div class="section-title">理쒖떊 蹂댁쑀???쒖쐞 <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ tic_updated_at }} 湲곗? 쨌 ??6二??꾪뻾 諛쒗몴</span></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>#</th><th>援??</th><th style="text-align:right;">蹂댁쑀??(B)</th><th style="text-align:right;">?꾩썡驪?/th><th style="text-align:right;">鍮꾩쨷</th></tr></thead>
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
    <b style="color:#cc0000;">TIC ?곗씠?곕??</b><br>
    Treasury International Capital ??誘??щТ遺媛 留ㅼ썡 諛쒗몴?섎뒗 ?멸뎅?몄쓽 誘멸뎅梨?蹂댁쑀 ?꾪솴. 以묎뎅쨌?쇰낯??蹂댁쑀??蹂?붾뒗 ?щ윭 ?④텒 諛?誘멸뎅梨?湲덈━???곹뼢??誘몄튂???듭떖 吏??<br><br>
    <b style="color:#555;">諛쒗몴 ?쇱젙 (留ㅼ썡 18?쇨꼍):</b><br>
    &nbsp;쨌 1???곗씠????3??18??諛쒗몴<br>
    &nbsp;쨌 2???곗씠????4??18??諛쒗몴<br>
    &nbsp;쨌 3???곗씠????5??18??諛쒗몴<br>
    &nbsp;쨌 <i>?댄븯 ?숈씪 ????긽 ??6二??꾪뻾</i><br><br>
    <b style="color:#555;">二쇱쓽:</b> 蹂댁쑀?됱? custodian 湲곗? ??以묎뎅 ?ъ옄?먭? 踰④린????됱뿉 ?덊긽 ??踰④린?먮줈 吏묎퀎. 猷⑹뀍遺瑜댄겕쨌耳?대㎤쨌踰④린????湲덉쑖 ?덈툕???믪? ?섏튂???ㅼ젣 ?대떦援?씠 ?꾨땶 ??援??먭툑??媛?μ꽦???믪쓬.
  </div>

{% endif %}
</div>

  <div class="footer">
    Net Liquidity: <a href="https://fred.stlouisfed.org" target="_blank" style="color:#60a5fa;text-decoration:none;">FRED</a> (WALCL쨌WDTGAL쨌RRPONTSYD쨌SP500) &nbsp;|&nbsp;
    TGA ?ъ슜泥? <a href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank" style="color:#60a5fa;text-decoration:none;">fiscaldata.treasury.gov</a> &nbsp;|&nbsp;
    援??蹂?誘멸뎅梨? <a href="https://home.treasury.gov/data/treasury-international-capital-tic-system" target="_blank" style="color:#60a5fa;text-decoration:none;">U.S. Treasury TIC</a> &nbsp;|&nbsp; 2000?뱎resent
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
        raise ValueError(f"{series_id}: ?곗씠???놁쓬")
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
    raise ValueError(f"{series_id}: ?ъ슜 媛?ν븳 frequency ?놁쓬")


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

    # ?몃뜳??= RRP ?쇨컙 湲곗? (媛??珥섏킌???쒕━利?
    # TGA(二쇨컙)/WALCL(二쇨컙)? ffill濡?梨꾩썙 ?쇨컙 ?됱뿉 諛섏쁺
    df = pd.DataFrame({"RRP": rrp_d}).sort_index()
    df["TGA"]   = tga_d.reindex(df.index, method="ffill")
    df["WALCL"] = walcl_w.reindex(df.index, method="ffill")
    df["SP500"] = spx_d.reindex(df.index, method="ffill")
    df = df.dropna(subset=["RRP", "WALCL", "TGA"])
    df["NL"] = df["WALCL"] - df["TGA"] - df["RRP"]
    df["NL_DoD"] = df["NL"].diff()

    valid = df[["NL", "SP500"]].dropna()
    model_info = None
    if len(valid) >= 10:
        x, y = valid["NL"].values, valid["SP500"].values
        slope, intercept = np.polyfit(x, y, 1)
        r2 = np.corrcoef(x, y)[0, 1] ** 2
        print(f"  ?뚭? R짼={r2:.3f}")
        df["FV_NL"] = slope * df["NL"] + intercept
        model_info = {"slope": f"{slope:.5f}", "intercept": f"{intercept:.1f}",
                      "r2": f"{r2:.3f}", "n": f"{len(valid):,}"}
    else:
        df["FV_NL"] = np.nan

    print(f"[{datetime.now().strftime('%H:%M:%S')}] NL ?꾨즺: {len(df)}媛?)
    return df, model_info


def _parse_hist(text):
    """mfhhis01.txt ?뚯떛: ?곕룄 ?ㅻ뜑 + 12媛쒖썡 而щ읆 ?щ㎎"""
    records = []
    current_year = None
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        if parts[0] == "Country":
            years = [p for p in parts[1:] if re.match(r"^\d{4}$", p)]
            if years:
                current_year = int(years[0])
            continue
        if current_year is None:
            continue
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
                            "country": clean, "value": v
                        })
                break
    return records


def _parse_curr(text):
    """slt_table5.txt ?뚯떛: YYYY-MM 而щ읆 ?щ㎎ (理쒓렐 13媛쒖썡)"""
    records = []
    date_cols = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        if parts[0] == "Country":
            date_cols = [p for p in parts[1:] if re.match(r"^\d{4}-\d{2}$", p)]
            continue
        if not date_cols:
            continue
        raw_name = parts[0].strip('"').strip()
        for country in TIC_COUNTRIES:
            clean = country.replace('"', '').strip()
            if raw_name == clean:
                nums = []
                for p in parts[1:]:
                    try:
                        nums.append(float(p.replace(',', '')))
                    except ValueError:
                        nums.append(None)
                for i, date_str in enumerate(date_cols):
                    if i < len(nums) and nums[i] is not None:
                        records.append({
                            "date": pd.to_datetime(date_str + "-01"),
                            "country": clean, "value": nums[i]
                        })
                break
    return records


def fetch_tic_data():
    """
    ?덉뒪?좊━(mfhhis01) + 理쒖떊(slt_table5) 蹂묓빀
    ??2000???꾩옱 ?꾩껜 ?곗씠???좎??섎㈃??理쒖떊 ??利됱떆 諛섏쁺
    """
    r_hist = req.get(TIC_URL_HIST, timeout=30)
    r_hist.raise_for_status()
    r_curr = req.get(TIC_URL_CURR, timeout=30)
    r_curr.raise_for_status()

    records = _parse_hist(r_hist.text) + _parse_curr(r_curr.text)

    if not records:
        raise ValueError("TIC ?곗씠???뚯떛 ?ㅽ뙣")

    df = pd.DataFrame(records)
    # 以묐났 ?쒓굅: slt_table5(理쒖떊) ?곗씠???곗꽑 (sort ??keep=last)
    df = df.sort_values("date").drop_duplicates(subset=["date", "country"], keep="last")
    pivot = df.pivot(index="date", columns="country", values="value").sort_index()
    pivot = pivot[pivot.index >= "2000-01-01"]
    latest = pivot.index[-1].strftime("%Y-%m")
    print(f"TIC ?꾨즺: {len(pivot)}媛??ъ씤?? {len(pivot.columns)}媛쒓뎅, 理쒖떊={latest}")
    return pivot


def fmt_mil(v):
    """諛깅쭔 ?щ윭(DTS ?⑥쐞) ???쎄린 ?ъ슫 臾몄옄??""
    try:
        v = float(str(v).replace(",", ""))
    except Exception:
        return "??
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}T"
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f}B"
    return f"{v:,.0f}M"


def fetch_dts_data():
    """
    Fiscal Data API ??DTS Table II (?낆텧湲? + Table I (?붿븸)
    踰좎씠??URL: https://api.fiscaldata.treasury.gov/services/api/fiscal_service/
    ?⑥쐞: 諛깅쭔 ?щ윭 (Millions of dollars)
    """
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1"
    EXCLUDE_CATG = {"Total Deposits", "Total Withdrawals", "Total",
                    "Subtotal", "Grand Total", ""}

    # ?? Table II: ?낆텧湲?(deposits_withdrawals_operating_cash) ??
    url_t2 = (
        f"{base}/accounting/dts/deposits_withdrawals_operating_cash"
        f"?fields=record_date,transaction_catg,transaction_type,transaction_today_amt"
        f"&sort=-record_date"
        f"&page[size]=300"
    )
    r2 = req.get(url_t2, timeout=30)
    r2.raise_for_status()
    data2 = r2.json().get("data", [])
    if not data2:
        raise ValueError("DTS Table II ?곗씠???놁쓬")

    latest_date = data2[0]["record_date"]
    day_data = [d for d in data2 if d["record_date"] == latest_date]

    deposits, withdrawals = {}, {}
    for d in day_data:
        catg = d.get("transaction_catg", "").strip()
        ttype = d.get("transaction_type", "").strip()
        try:
            amt = float((d.get("transaction_today_amt") or "0").replace(",", ""))
        except Exception:
            amt = 0.0
        if "Deposit" in ttype:
            deposits[catg] = deposits.get(catg, 0) + amt
        elif "Withdrawal" in ttype:
            withdrawals[catg] = withdrawals.get(catg, 0) + amt

    dep_sorted = sorted(
        [(k, v) for k, v in deposits.items() if k not in EXCLUDE_CATG and v > 0],
        key=lambda x: x[1], reverse=True
    )[:8]
    wit_sorted = sorted(
        [(k, v) for k, v in withdrawals.items() if k not in EXCLUDE_CATG and v > 0],
        key=lambda x: x[1], reverse=True
    )[:8]

    dep_list = [{"name": k, "amt": fmt_mil(v)} for k, v in dep_sorted]
    wit_list = [{"name": k, "amt": fmt_mil(v)} for k, v in wit_sorted]

    # ?? TGA ?뱀씪 ?낆텧湲??붿빟: 吏곸젒 怨꾩궛 ??
    total_dep = sum(deposits.values())
    total_wit = sum(withdrawals.values())
    net = total_dep - total_wit
    balance_list = [
        {"name": "珥??낃툑 (Total Deposits)",    "amt": fmt_mil(total_dep), "pos": True},
        {"name": "珥?異쒓툑 (Total Withdrawals)", "amt": fmt_mil(total_wit), "pos": False},
        {"name": f"?뱀씪 ?쒕???({'?좎엯' if net>=0 else '?좎텧'})", "amt": fmt_mil(abs(net)), "pos": net >= 0},
    ]

    print(f"DTS ?꾨즺: {latest_date}, ?낃툑{len(dep_list)}嫄?異쒓툑{len(wit_list)}嫄?net={fmt_mil(net)}")
    return dep_list, wit_list, balance_list, latest_date


TIP_INFO = {
    "Bill": {
        "title": "Treasury Bill",
        "body":  "留뚭린 1???댄븯 ?④린 援?콈. MMF媛 二쇱슂 留ㅼ닔????T-Bill 諛쒗뻾????RRP???곸뇙 ??NL 異⑷꺽 ?쒗븳.",
        "liq":   "NL ?곹뼢 ?쒗븳 (RRP ?곸뇙)", "neg": False,
    },
    "Note": {
        "title": "Treasury Note (2~10Y)",
        "body":  "以묎린 援?콈. ??됀룹뿰湲곌툑 留ㅼ닔 ??以鍮꾧툑 吏곸젒 ?≪닔 ??NL ?섎씫 ?뺣젰. 荑좏룿梨?鍮꾩쨷??= 湲댁텞 ?좏샇.",
        "liq":   "???以鍮꾧툑 ?≪닔 ??NL??, "neg": True,
    },
    "Bond": {
        "title": "Treasury Bond (20~30Y)",
        "body":  "?κ린 援?콈. ??덉씠???믪븘 ?κ린 湲덈━ 誘쇨컧. 湲덈━????二쇱떇 硫?고뵆 ?뺣컯 ??NL ?④낵 ?댁긽 異⑷꺽 媛??",
        "liq":   "?κ린湲덈━ 寃쎈줈濡?媛꾩젒 NL ?뺣컯", "neg": True,
    },
    "TIPS": {
        "title": "TIPS (臾쇨??곕룞)",
        "body":  "?먭툑??CPI???곕룞. ?ㅼ쭏湲덈━ 吏?쒕줈 ?쒖슜. TIPS 湲덈━??= 湲댁텞 ?좏샇. 吏곸젒 NL ?④낵???쒗븳??",
        "liq":   "?ㅼ쭏湲덈━ 吏????吏곸젒 ?④낵 ?쒗븳??, "neg": False,
    },
    "FRN": {
        "title": "FRN (蹂?숆툑由ъ콈)",
        "body":  "13二?T-Bill 湲덈━???곕룞??蹂?숆툑由?援?콈. 湲덈━ ?곸듅湲??ъ옄???좏샇. ?④린臾쇱뿉 媛源뚯슫 ?좊룞???뱀꽦.",
        "liq":   "?④린臾??좎궗 ??NL ?곹뼢 ?쒗븳??, "neg": False,
    },
}


def fetch_qra_data():
    """TreasuryDirect TA_WS API ??理쒓렐 30??寃쎈ℓ ?곗씠??""
    from datetime import datetime, timedelta
    import pytz
    now = datetime.now(pytz.utc)
    start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    end   = now.strftime("%Y-%m-%d")

    url = (
        "https://www.treasurydirect.gov/TA_WS/securities/auctioned"
        f"?format=json&dateFieldName=auctionDate&startDate={start}&endDate={end}"
    )
    r = req.get(url, timeout=30)
    r.raise_for_status()
    raw = r.json()
    if not raw:
        raise ValueError("QRA 寃쎈ℓ ?곗씠???놁쓬")

    TYPE_MAP = {
        "Bill":  {"label": "T-Bill", "bg": "rgba(248,113,113,0.12)", "color": "#f87171"},
        "Note":  {"label": "Note",   "bg": "rgba(96,165,250,0.12)",  "color": "#60a5fa"},
        "Bond":  {"label": "Bond",   "bg": "rgba(251,191,36,0.12)",  "color": "#fbbf24"},
        "TIPS":  {"label": "TIPS",   "bg": "rgba(167,139,250,0.12)", "color": "#a78bfa"},
        "FRN":   {"label": "FRN",    "bg": "rgba(52,211,153,0.12)",  "color": "#34d399"},
    }

    tbill = note = bond = tips = total = 0.0
    btc_list = []
    auctions = []

    for d in raw:
        stype = d.get("securityType", "")
        term  = d.get("securityTerm", "")
        date  = (d.get("auctionDate") or "")[:10]
        try:
            amt = float(d.get("totalAccepted") or d.get("competitiveAccepted") or 0) / 1e9
        except Exception:
            amt = 0.0
        try:
            btc = float(d.get("bidToCoverRatio") or 0)
        except Exception:
            btc = 0.0
        rate_raw = d.get("highDiscountRate") or d.get("highYield") or d.get("interestRate") or ""
        try:
            rate = f"{float(rate_raw):.3f}%"
        except Exception:
            rate = "??

        total += amt
        if stype == "Bill":   tbill += amt
        elif stype == "Note": note  += amt
        elif stype == "Bond": bond  += amt
        elif stype == "TIPS": tips  += amt

        if btc > 0:
            btc_list.append(btc)

        tm = TYPE_MAP.get(stype, {"label": stype, "bg": "rgba(255,255,255,0.05)", "color": "rgba(255,255,255,0.3)"})
        ti = TIP_INFO.get(stype, TIP_INFO["Note"])
        auctions.append({
            "date": date, "stype": tm["label"], "term": term,
            "amt": f"{amt:.1f}",
            "btc": f"{btc:.2f}x" if btc > 0 else "??,
            "btc_ok": btc >= 2.3,
            "rate": rate,
            "type_bg": tm["bg"], "type_color": tm["color"],
            "tip_title": ti["title"],
            "tip_body":  ti["body"],
            "tip_liq":   ti["liq"],
            "tip_neg":   ti["neg"],
            "is_bill":   stype == "Bill",
        })

    auctions = sorted(auctions, key=lambda x: x["date"], reverse=True)[:20]
    avg_btc = sum(btc_list) / len(btc_list) if btc_list else 0

    # 諛쒗뻾 援ъ꽦 諛?(理쒕?媛?湲곗? %)
    max_v = max(tbill, note, bond, tips, 0.1)
    def pct(v): return round(v / max_v * 95)
    breakdown = [
        {"label": "T-Bills",     "amt": f"${tbill:.0f}B", "pct": pct(tbill), "color": "#f87171"},
        {"label": "Notes(2~7Y)", "amt": f"${note:.0f}B",  "pct": pct(note),  "color": "#60a5fa"},
        {"label": "Bonds(10~30Y)","amt": f"${bond:.0f}B", "pct": pct(bond),  "color": "#fbbf24"},
        {"label": "TIPS",        "amt": f"${tips:.0f}B",  "pct": pct(tips),  "color": "#a78bfa"},
    ]

    # QRA 諛쒗몴 ?쇱젙 (遺꾧린 留덉?留??붿슂??湲곗? ~4?붾쭚쨌7?붾쭚쨌10?붾쭚쨌1?붾쭚)
    schedule = [
        {"label": "Q1: 2026-01-27 ?꾨즺", "current": False},
        {"label": "Q2: 2026-04-28 ?덉젙", "current": True},
        {"label": "Q3: 2026-07-27 ?덉젙", "current": False},
        {"label": "Q4: 2026-10-27 ?덉젙", "current": False},
    ]

    def fmt_b(v): return f"${v:.0f}B" if v >= 1 else f"${v*1000:.0f}M"

    print(f"QRA ?꾨즺: {len(raw)}嫄? total={fmt_b(total)}, bill={fmt_b(tbill)}, note={fmt_b(note)}")
    return {
        "next_qra":   "2026-04-28",
        "tbill_30d":  fmt_b(tbill),
        "coupon_30d": fmt_b(note + bond),
        "tips_30d":   fmt_b(tips),
        "total_30d":  fmt_b(total),
        "avg_btc":    f"{avg_btc:.2f}x" if avg_btc else "??,
        "breakdown":  breakdown,
        "schedule":   schedule,
        "auctions":   auctions,
        "start_date": start,
    }


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
        fv_nl_gap = f"{'+' if gap>0 else ''}{gap:.1f}% {'怨좏룊媛' if gap>0 else '??됯?'}"
        fv_nl_cheap = gap < 0

    return {
        "base_date": df.index[-1].strftime("%Y-%m-%d"),
        "nl": fmt_val(latest["NL"]), "nl_raw": f"{latest['NL']:,.0f}B",
        "nl_chg": f"{'?? if chg>=0 else '??} {fmt_val(abs(chg))} DoD", "nl_chg_pos": chg >= 0,
        "walcl": fmt_val(latest["WALCL"]), "walcl_raw": f"{latest['WALCL']:,.0f}B",
        "walcl_date": walcl_date.strftime("%m-%d") if walcl_date else "??,
        "tga": fmt_val(latest["TGA"]), "tga_raw": f"{latest['TGA']:,.0f}B",
        "tga_date": tga_date.strftime("%m-%d") if tga_date else "??,
        "rrp": fmt_val(latest["RRP"]), "rrp_raw": f"{latest['RRP']:,.0f}B",
        "rrp_date": rrp_date.strftime("%m-%d") if rrp_date else "??,
        "spx_raw": f"{spx:,.0f}" if spx else "??,
        "fv_nl": f"{fv_nl:,.0f}" if fv_nl else "??,
        "fv_nl_gap": fv_nl_gap or "?곗씠??遺議?, "fv_nl_cheap": fv_nl_cheap,
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
            "dod": f"{'?? if dod>=0 else '??}{abs(dod):,.0f}" if dod is not None else "??,
            "dod_pos": dod >= 0 if dod is not None else None,
            "spx": f"{spx:,.0f}" if spx else "??,
            "fv_nl": f"{fv_nl:,.0f}" if fv_nl else "??,
            "gap": gap, "gap_pos": gap_pos,
        })
    return list(reversed(rows[-10:]))


def build_chart1(df):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(255,255,255,0.04)", layer="below", line_width=0)
    # ?ㅽ깮 ?쒖꽌: RRP(諛붾떏) ??TGA(以묎컙) ??NL(?곷떒) = WALCL ?꾩껜
    fig.add_trace(go.Scatter(x=df.index, y=df["RRP"], name="RRP",
        line=dict(color="#fbbf24", width=0.8),
        fill="tozeroy", fillcolor="rgba(251,191,36,0.4)",
        stackgroup="walcl"))
    fig.add_trace(go.Scatter(x=df.index, y=df["TGA"], name="TGA",
        line=dict(color="#34d399", width=0.8),
        fill="tonexty", fillcolor="rgba(52,211,153,0.4)",
        stackgroup="walcl"))
    fig.add_trace(go.Scatter(x=df.index, y=df["NL"], name="Net Liquidity",
        line=dict(color="#60a5fa", width=1.5),
        fill="tonexty", fillcolor="rgba(96,165,250,0.5)",
        stackgroup="walcl"))
    grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", gridwidth=0.5, griddash="dot",
                linecolor="rgba(255,255,255,0.08)", linewidth=1, showline=True, ticks="outside", tickcolor="rgba(255,255,255,0.1)",
                tickfont=dict(size=10, color="rgba(255,255,255,0.35)"))
    fig.update_layout(height=320, plot_bgcolor="rgba(255,255,255,0.02)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system,BlinkMacSystemFont,sans-serif", size=11, color="rgba(255,255,255,0.5)"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Billions USD", title_font=dict(size=10, color="rgba(255,255,255,0.3)"),
                     tickformat=",", ticksuffix="B")
    return fig.to_html(include_plotlyjs="cdn", full_html=False, config={"displayModeBar": False})


def build_chart2(df):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fiscal_events = [
        {"month": 2,  "label": "?섍툒 ?쇳겕", "color": "rgba(52,211,153,0.5)"},
        {"month": 3,  "label": "?섍툒 ?쇳겕", "color": "rgba(52,211,153,0.5)"},
        {"month": 4,  "label": "Tax Day",   "color": "rgba(248,113,113,0.6)"},
        {"month": 6,  "label": "2Q 異붿젙??, "color": "rgba(251,191,36,0.5)"},
        {"month": 9,  "label": "3Q 異붿젙??, "color": "rgba(251,191,36,0.5)"},
        {"month": 1,  "label": "4Q 異붿젙??, "color": "rgba(251,191,36,0.5)"},
    ]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(255,255,255,0.03)", layer="below", line_width=0)
    # ?ъ젙 ?대깽???ㅻ쾭?덉씠 (理쒓렐 3?꾩튂留?
    years = list(range(df.index[-1].year - 2, df.index[-1].year + 1))
    for yr in years:
        for ev in fiscal_events:
            try:
                x_date = f"{yr}-{ev['month']:02d}-15"
                fig.add_vline(x=x_date, line_width=1, line_dash="dot", line_color=ev["color"],
                              annotation_text=ev["label"] if yr == years[-1] else "",
                              annotation_font_size=9, annotation_font_color=ev["color"],
                              annotation_position="top left")
            except Exception:
                pass
    fig.add_trace(go.Scatter(x=df.index, y=df["SP500"], name="S&P 500",
        line=dict(color="#e2e2e2", width=2)))
    if "FV_NL" in df.columns and df["FV_NL"].notna().any():
        fig.add_trace(go.Scatter(x=df.index, y=df["FV_NL"], name="NL ?뚭? FV",
            line=dict(color="#60a5fa", width=1.5, dash="dot")))
    spx_vals = df["SP500"].dropna()
    spx_min = int(spx_vals.min() * 0.9) if len(spx_vals) else 500
    spx_max = int(spx_vals.max() * 1.05) if len(spx_vals) else 7500
    grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", gridwidth=0.5, griddash="dot",
                linecolor="rgba(255,255,255,0.08)", linewidth=1, showline=True, ticks="outside", tickcolor="rgba(255,255,255,0.1)",
                tickfont=dict(size=10, color="rgba(255,255,255,0.35)"))
    fig.update_layout(height=320, plot_bgcolor="rgba(255,255,255,0.02)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system,BlinkMacSystemFont,sans-serif", size=11, color="rgba(255,255,255,0.5)"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Index Level", title_font=dict(size=10, color="rgba(255,255,255,0.3)"),
                     tickformat=",", range=[spx_min, spx_max])
    return fig.to_html(include_plotlyjs=False, full_html=False, config={"displayModeBar": False})


def build_tic_chart(pivot):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(255,255,255,0.04)", layer="below", line_width=0)
    for country in TIC_COUNTRIES:
        clean = country.replace('"','')
        if clean not in pivot.columns:
            continue
        color = TIC_COLORS.get(clean, "#888888")
        dash = "dash" if clean in ["Luxembourg","Cayman Islands","Canada","Belgium"] else "solid"
        fig.add_trace(go.Scatter(x=pivot.index, y=pivot[clean], name=clean,
            line=dict(color=color, width=1.8, dash=dash)))
    grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", gridwidth=0.5, griddash="dot",
                linecolor="rgba(255,255,255,0.08)", linewidth=1, showline=True, ticks="outside", tickcolor="rgba(255,255,255,0.1)",
                tickfont=dict(size=10, color="rgba(255,255,255,0.35)"))
    fig.update_layout(height=380, plot_bgcolor="rgba(255,255,255,0.02)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system,BlinkMacSystemFont,sans-serif", size=11, color="rgba(255,255,255,0.5)"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Billions USD", title_font=dict(size=10, color="rgba(255,255,255,0.3)"), tickformat=",")
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
            "chg": f"{'+' if chg and chg>=0 else ''}{chg:.1f}" if chg is not None else "??,
            "chg_pos": chg >= 0 if chg is not None else True,
            "pct": f"{pct:.1f}", "bar_pct": bar_pct,
        })
    return rows[:15]





def next_thursday_kst():
    now = datetime.now(KST)
    days_ahead = (3 - now.weekday()) % 7
    if days_ahead == 0 and now.hour >= 6:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).strftime("%m-%d")


def refresh_nl():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] NL 媛깆떊 ?쒖옉...")
    try:
        df, model_info = build_nl_data()
        cache["summary"]     = build_nl_summary(df)
        cache["chart1_html"] = build_chart1(df)
        cache["chart2_html"] = build_chart2(df)
        cache["table_rows"]  = build_nl_table(df)
        cache["model_info"]  = model_info
        cache["updated_at"]  = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        cache["next_h41"]    = next_thursday_kst()
        cache["error"] = None
        print(f"[{datetime.now().strftime('%H:%M:%S')}] NL ?꾨즺")
    except Exception as e:
        cache["error"] = str(e)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] NL ?ㅻ쪟: {e}")


def refresh_tic():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] TIC 媛깆떊 ?쒖옉...")
    try:
        pivot = fetch_tic_data()
        cache["tic_chart_html"] = build_tic_chart(pivot)
        cache["tic_table"]      = build_tic_table(pivot)
        cache["tic_updated_at"] = pivot.index[-1].strftime("%Y-%m")
        cache["tic_error"] = None
        print(f"[{datetime.now().strftime('%H:%M:%S')}] TIC ?꾨즺")
    except Exception as e:
        cache["tic_error"] = str(e)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] TIC ?ㅻ쪟: {e}")


def refresh_dts():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] DTS 媛깆떊 ?쒖옉...")
    try:
        dep, wit, bal, date = fetch_dts_data()
        cache["dts_deposits"]    = dep
        cache["dts_withdrawals"] = wit
        cache["dts_balance"]     = bal
        cache["dts_date"]        = date
        cache["dts_error"]       = None
        print(f"[{datetime.now().strftime('%H:%M:%S')}] DTS ?꾨즺: {date}")
    except Exception as e:
        cache["dts_error"] = str(e)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] DTS ?ㅻ쪟: {e}")


def refresh_qra():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] QRA 媛깆떊 ?쒖옉...")
    try:
        cache["qra_data"]  = fetch_qra_data()
        cache["qra_error"] = None
        print(f"[{datetime.now().strftime('%H:%M:%S')}] QRA ?꾨즺")
    except Exception as e:
        cache["qra_error"] = str(e)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] QRA ?ㅻ쪟: {e}")


def refresh_data():
    refresh_nl()
    refresh_tic()
    refresh_dts()
    refresh_qra()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=KST)
    scheduler.add_job(refresh_nl,  CronTrigger(hour=0,  minute=30, timezone=KST), id="rrp_daily")
    scheduler.add_job(refresh_nl,  CronTrigger(hour=7,  minute=0,  timezone=KST), id="spx_daily")
    scheduler.add_job(refresh_nl,  CronTrigger(day_of_week="thu", hour=5, minute=30, timezone=KST), id="h41_weekly")
    scheduler.add_job(refresh_tic, CronTrigger(day=18,  hour=2,   minute=0,  timezone=KST), id="tic_monthly")
    scheduler.add_job(refresh_dts, CronTrigger(day_of_week="mon-fri", hour=9, minute=0,  timezone=KST), id="dts_daily")
    scheduler.add_job(refresh_qra, CronTrigger(day_of_week="mon-fri", hour=9, minute=10, timezone=KST), id="qra_daily")
    scheduler.start()
    print("?ㅼ?以꾨윭: RRP=00:30 / SPX=07:00 / H.4.1=紐?05:30 / TIC=18??02:00 / DTS=?됱씪 09:00 / QRA=?됱씪 09:10 (KST)")
    return scheduler


@app.route("/")
def index():
    tic_legend = [{"name": c.replace('"',''), "color": TIC_COLORS.get(c.replace('"',''), "#888")}
                  for c in TIC_COUNTRIES[:6]]
    return render_template_string(HTML_TEMPLATE,
        chart1_html=cache.get("chart1_html"),
        chart2_html=cache.get("chart2_html"),
        summary=cache["summary"],
        table_rows=cache["table_rows"] or [],
        updated_at=cache["updated_at"] or "??,
        error=cache["error"],
        model_info=cache["model_info"],
        tic_chart_html=cache.get("tic_chart_html"),
        tic_table=cache.get("tic_table") or [],
        tic_updated_at=cache.get("tic_updated_at") or "??,
        tic_error=cache.get("tic_error"),
        tic_legend=tic_legend,
        next_h41=cache.get("next_h41") or "??,
        dts_deposits=cache.get("dts_deposits") or [],
        dts_withdrawals=cache.get("dts_withdrawals") or [],
        dts_balance=cache.get("dts_balance") or [],
        dts_date=cache.get("dts_date") or "??,
        dts_error=cache.get("dts_error"),
        qra_data=cache.get("qra_data"),
        qra_error=cache.get("qra_error"),
    )


@app.route("/refresh")
def manual_refresh():
    threading.Thread(target=refresh_data, daemon=False).start()
    return "ok"


@app.route("/health")
def health():
    return "ok"


threading.Thread(target=lambda: (refresh_data(), start_scheduler()), daemon=False).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
