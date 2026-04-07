"""
Net Liquidity + 국가별 미국채 보유 Dashboard
=============================================
Vercel + Neon(PostgreSQL) 버전

환경변수:
  FRED_API_KEY  : FRED API Key
  DATABASE_URL  : Neon PostgreSQL 연결 문자열
  START_DATE    : 시작일 (기본 2000-01-01)
  CRON_SECRET   : Cron 엔드포인트 보호용 시크릿 키

업데이트 스케줄 (vercel.json cron):
  - NL/DTS/QRA : 매일 00:30 UTC
  - TIC        : 매월 18일 02:00 UTC
"""

import os
import re
import json
import threading
import requests as req
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
from flask import Flask, render_template_string, request, jsonify
from datetime import datetime, timedelta
import pytz

API_KEY      = os.environ.get("FRED_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
START_DATE   = os.environ.get("START_DATE", "2000-01-01")
CRON_SECRET  = os.environ.get("CRON_SECRET", "")
PORT         = int(os.environ.get("PORT", "5000"))
KST          = pytz.timezone("Asia/Seoul")

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
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

app = Flask(__name__)


# ──────────────────────────────────────────────
# Neon DB 유틸
# ──────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """캐시 테이블 초기화 (최초 1회)"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key   TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()


def db_get(key):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM cache WHERE key = %s", (key,))
                row = cur.fetchone()
                return json.loads(row[0]) if row else None
    except Exception as e:
        print(f"[DB GET ERROR] {key}: {e}")
        return None


def db_set(key, value):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cache (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                      SET value = EXCLUDED.value,
                          updated_at = NOW()
                """, (key, json.dumps(value)))
            conn.commit()
    except Exception as e:
        print(f"[DB SET ERROR] {key}: {e}")


def db_get_updated_at(key):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT updated_at FROM cache WHERE key = %s", (key,))
                row = cur.fetchone()
                if row:
                    return row[0].astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
                return None
    except Exception:
        return None


# ──────────────────────────────────────────────
# FRED 데이터 fetch
# ──────────────────────────────────────────────

def fetch_series(series_id, start, frequency="d"):
    if not API_KEY:
        raise ValueError("FRED_API_KEY 환경변수가 설정되지 않았습니다.")
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
    for freq in dict.fromkeys([preferred, "w", "bw", "m"]):
        try:
            s = fetch_series(series_id, start, frequency=freq)
            if len(s) > 0:
                return s, freq
        except Exception:
            continue
    raise ValueError(f"{series_id}: 사용 가능한 frequency 없음")


# ──────────────────────────────────────────────
# NL 계산
# ──────────────────────────────────────────────

def fmt_val(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    if abs(v) >= 1_000:
        return f"{v/1_000:.2f}T"
    return f"{v:,.0f}B"


def build_nl_data():
    walcl_w = fetch_series("WALCL", START_DATE, frequency="w")
    tga_d, _ = fetch_auto("WDTGAL", START_DATE, preferred="w")
    rrp_d, _ = fetch_auto("RRPONTSYD", START_DATE, preferred="d")
    try:
        spx_d, _ = fetch_auto("SP500", START_DATE, preferred="d")
    except Exception:
        spx_d = pd.Series(dtype=float, name="SP500")

    # yfinance fallback
    try:
        import yfinance as yf
        yf_spx = yf.download("^GSPC", start=START_DATE, progress=False, auto_adjust=True)["Close"]
        yf_spx.index = __import__("pandas").to_datetime(yf_spx.index).tz_localize(None)
        yf_spx.name = "SP500"
        missing = yf_spx.index.difference(spx_d.index)
        if len(missing) > 0:
            spx_d = __import__("pandas").concat([spx_d, yf_spx.loc[missing]]).sort_index()
    except Exception:
        pass

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
        df["FV_NL"] = slope * df["NL"] + intercept
        model_info = {"slope": f"{slope:.5f}", "intercept": f"{intercept:.1f}",
                      "r2": f"{r2:.3f}", "n": f"{len(valid):,}"}
    else:
        df["FV_NL"] = np.nan

    return df, model_info


def build_nl_summary(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    spx = latest["SP500"] if not pd.isna(latest["SP500"]) else None
    fv_nl = latest["FV_NL"] if "FV_NL" in latest.index and not pd.isna(latest["FV_NL"]) else None
    chg = float(latest["NL"]) - float(prev["NL"]) if prev is not None and not pd.isna(latest["NL"]) and not pd.isna(prev["NL"]) else 0

    walcl_date = df["WALCL"].last_valid_index()
    tga_date   = df["TGA"].last_valid_index()
    rrp_date   = df["RRP"].last_valid_index()

    fv_nl_gap = fv_nl_cheap = None
    if fv_nl is not None and spx is not None and fv_nl != 0:
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
            gap_pos = g >= 0
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "walcl": f"{row['WALCL']:,.0f}", "tga": f"{row['TGA']:,.0f}", "rrp": f"{row['RRP']:,.0f}",
            "nl": f"{row['NL']:,.0f}",
            "dod": f"{'▲' if dod>0 else ('▼' if dod<0 else '─')}{abs(round(dod)):,.0f}" if dod is not None else "—",
            "dod_pos": None if dod is None or round(dod)==0 else dod > 0,
            "spx": f"{spx:,.0f}" if spx else "—",
            "fv_nl": f"{fv_nl:,.0f}" if fv_nl else "—",
            "gap": gap, "gap_pos": gap_pos,
        })
    return list(reversed(rows[-10:]))


def build_chart1(df):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(255,255,255,0.04)", layer="below", line_width=0)
    fig.add_trace(go.Scatter(x=df.index.strftime("%Y-%m-%d").tolist(), y=df["RRP"].tolist(), name="RRP",
        line=dict(color="#fbbf24", width=0.8),
        fill="tozeroy", fillcolor="rgba(251,191,36,0.4)", stackgroup="walcl"))
    fig.add_trace(go.Scatter(x=df.index.strftime("%Y-%m-%d").tolist(), y=df["TGA"].tolist(), name="TGA",
        line=dict(color="#34d399", width=0.8),
        fill="tonexty", fillcolor="rgba(52,211,153,0.4)", stackgroup="walcl"))
    fig.add_trace(go.Scatter(x=df.index.strftime("%Y-%m-%d").tolist(), y=df["NL"].tolist(), name="Net Liquidity",
        line=dict(color="#60a5fa", width=1.5),
        fill="tonexty", fillcolor="rgba(96,165,250,0.5)", stackgroup="walcl"))
    grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", gridwidth=0.5, griddash="dot",
                linecolor="rgba(255,255,255,0.08)", linewidth=1, showline=True, ticks="outside",
                tickcolor="rgba(255,255,255,0.1)", tickfont=dict(size=10, color="rgba(255,255,255,0.35)"))
    fig.update_layout(height=320, plot_bgcolor="rgba(255,255,255,0.02)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system,BlinkMacSystemFont,sans-serif", size=11, color="rgba(255,255,255,0.5)"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Billions USD",
                     title_font=dict(size=10, color="rgba(255,255,255,0.3)"),
                     tickformat=",", ticksuffix="B")
    return fig.to_html(include_plotlyjs=False, full_html=False, config={"displayModeBar": False})


def build_chart2(df):
    recession_periods = [("2001-03-01","2001-11-01"),("2007-12-01","2009-06-01"),("2020-02-01","2020-04-01")]
    fiscal_events = [
        {"month": 2, "label": "환급 피크", "color": "rgba(52,211,153,0.5)"},
        {"month": 3, "label": "환급 피크", "color": "rgba(52,211,153,0.5)"},
        {"month": 4, "label": "Tax Day",   "color": "rgba(248,113,113,0.6)"},
        {"month": 6, "label": "2Q 추정세", "color": "rgba(251,191,36,0.5)"},
        {"month": 9, "label": "3Q 추정세", "color": "rgba(251,191,36,0.5)"},
        {"month": 1, "label": "4Q 추정세", "color": "rgba(251,191,36,0.5)"},
    ]
    fig = go.Figure()
    for s, e in recession_periods:
        fig.add_vrect(x0=s, x1=e, fillcolor="rgba(255,255,255,0.03)", layer="below", line_width=0)
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
    fig.add_trace(go.Scatter(x=df.index.strftime("%Y-%m-%d").tolist(), y=df["SP500"].tolist(),
        name="S&P 500", line=dict(color="#e2e2e2", width=2)))
    if "FV_NL" in df.columns and df["FV_NL"].notna().any():
        fig.add_trace(go.Scatter(x=df.index.strftime("%Y-%m-%d").tolist(), y=df["FV_NL"].tolist(),
            name="NL 회귀 FV", line=dict(color="#60a5fa", width=1.5, dash="dot")))
    spx_vals = df["SP500"].dropna()
    spx_min = int(spx_vals.min() * 0.9) if len(spx_vals) else 500
    spx_max = int(spx_vals.max() * 1.05) if len(spx_vals) else 7500
    grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", gridwidth=0.5, griddash="dot",
                linecolor="rgba(255,255,255,0.08)", linewidth=1, showline=True, ticks="outside",
                tickcolor="rgba(255,255,255,0.1)", tickfont=dict(size=10, color="rgba(255,255,255,0.35)"))
    fig.update_layout(height=320, plot_bgcolor="rgba(255,255,255,0.02)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system,BlinkMacSystemFont,sans-serif", size=11, color="rgba(255,255,255,0.5)"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Index Level",
                     title_font=dict(size=10, color="rgba(255,255,255,0.3)"),
                     tickformat=",", range=[spx_min, spx_max])
    return fig.to_html(include_plotlyjs=False, full_html=False, config={"displayModeBar": False})


# ──────────────────────────────────────────────
# TIC
# ──────────────────────────────────────────────

def _parse_hist(text):
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
                        month_num = m_idx + 1
                        records.append({
                            "date": pd.to_datetime(f"{current_year}-{month_num:02d}-01"),
                            "country": clean, "value": v
                        })
                break
    return records


def _parse_curr(text):
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
    r_hist = req.get(TIC_URL_HIST, timeout=30)
    r_hist.raise_for_status()
    r_curr = req.get(TIC_URL_CURR, timeout=30)
    r_curr.raise_for_status()
    records = _parse_hist(r_hist.text) + _parse_curr(r_curr.text)
    if not records:
        raise ValueError("TIC 데이터 파싱 실패")
    df = pd.DataFrame(records)
    df = df.sort_values("date").drop_duplicates(subset=["date", "country"], keep="last")
    pivot = df.pivot(index="date", columns="country", values="value").sort_index()
    pivot = pivot[pivot.index >= "2000-01-01"]
    return pivot


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
        fig.add_trace(go.Scatter(
            x=pivot.index.strftime("%Y-%m-%d").tolist(),
            y=pivot[clean].tolist(), name=clean,
            line=dict(color=color, width=1.8, dash=dash)))
    grid = dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", gridwidth=0.5, griddash="dot",
                linecolor="rgba(255,255,255,0.08)", linewidth=1, showline=True, ticks="outside",
                tickcolor="rgba(255,255,255,0.1)", tickfont=dict(size=10, color="rgba(255,255,255,0.35)"))
    fig.update_layout(height=380, plot_bgcolor="rgba(255,255,255,0.02)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system,BlinkMacSystemFont,sans-serif", size=11, color="rgba(255,255,255,0.5)"),
        hovermode="x unified", margin=dict(t=10, b=40, l=70, r=20), showlegend=False)
    fig.update_xaxes(**grid)
    fig.update_yaxes(**grid, title_text="Billions USD",
                     title_font=dict(size=10, color="rgba(255,255,255,0.3)"), tickformat=",")
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


# ──────────────────────────────────────────────
# DTS
# ──────────────────────────────────────────────

def fmt_mil(v):
    try:
        v = float(str(v).replace(",", ""))
    except Exception:
        return "—"
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}T"
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f}B"
    return f"{v:,.0f}M"


def fetch_dts_data():
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1"
    EXCLUDE_CATG = {"Total Deposits", "Total Withdrawals", "Total", "Subtotal", "Grand Total", ""}
    url_t2 = (
        f"{base}/accounting/dts/deposits_withdrawals_operating_cash"
        f"?fields=record_date,transaction_catg,transaction_type,transaction_today_amt"
        f"&sort=-record_date&page[size]=300"
    )
    r2 = req.get(url_t2, timeout=30)
    r2.raise_for_status()
    data2 = r2.json().get("data", [])
    if not data2:
        raise ValueError("DTS Table II 데이터 없음")

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
        key=lambda x: x[1], reverse=True)[:8]
    wit_sorted = sorted(
        [(k, v) for k, v in withdrawals.items() if k not in EXCLUDE_CATG and v > 0],
        key=lambda x: x[1], reverse=True)[:8]

    dep_list = [{"name": k, "amt": fmt_mil(v)} for k, v in dep_sorted]
    wit_list = [{"name": k, "amt": fmt_mil(v)} for k, v in wit_sorted]

    total_dep = sum(deposits.values())
    total_wit = sum(withdrawals.values())
    net = total_dep - total_wit
    balance_list = [
        {"name": "총 입금 (Total Deposits)",    "amt": fmt_mil(total_dep), "pos": True},
        {"name": "총 출금 (Total Withdrawals)", "amt": fmt_mil(total_wit), "pos": False},
        {"name": f"당일 순변동 ({'유입' if net>=0 else '유출'})", "amt": fmt_mil(abs(net)), "pos": net >= 0},
    ]
    return dep_list, wit_list, balance_list, latest_date


# ──────────────────────────────────────────────
# QRA
# ──────────────────────────────────────────────

TIP_INFO = {
    "Bill": {"title": "Treasury Bill", "body": "만기 1년 이하 단기 국채. MMF가 주요 매수자 — T-Bill 발행↑ → RRP↓ 상쇄 → NL 충격 제한.", "liq": "NL 영향 제한 (RRP 상쇄)", "neg": False},
    "Note": {"title": "Treasury Note (2~10Y)", "body": "중기 국채. 은행·연기금 매수 시 준비금 직접 흡수 → NL 하락 압력.", "liq": "은행 준비금 흡수 → NL↓", "neg": True},
    "Bond": {"title": "Treasury Bond (20~30Y)", "body": "장기 국채. 듀레이션 높아 장기 금리 민감.", "liq": "장기금리 경로로 간접 NL 압박", "neg": True},
    "TIPS": {"title": "TIPS (물가연동)", "body": "원금이 CPI에 연동. 실질금리 지표.", "liq": "실질금리 지표 — 직접 효과 제한적", "neg": False},
    "FRN":  {"title": "FRN (변동금리채)", "body": "13주 T-Bill 금리에 연동. 단기물에 가까운 유동성 특성.", "liq": "단기물 유사 — NL 영향 제한적", "neg": False},
}


def fetch_qra_data():
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
        raise ValueError("QRA 경매 데이터 없음")

    TYPE_MAP = {
        "Bill": {"label": "T-Bill", "bg": "rgba(248,113,113,0.12)", "color": "#f87171"},
        "Note": {"label": "Note",   "bg": "rgba(96,165,250,0.12)",  "color": "#60a5fa"},
        "Bond": {"label": "Bond",   "bg": "rgba(251,191,36,0.12)",  "color": "#fbbf24"},
        "TIPS": {"label": "TIPS",   "bg": "rgba(167,139,250,0.12)", "color": "#a78bfa"},
        "FRN":  {"label": "FRN",    "bg": "rgba(52,211,153,0.12)",  "color": "#34d399"},
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
            rate = "—"

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
            "amt": f"{amt:.1f}", "btc": f"{btc:.2f}x" if btc > 0 else "—",
            "btc_ok": btc >= 2.3, "rate": rate,
            "type_bg": tm["bg"], "type_color": tm["color"],
            "tip_title": ti["title"], "tip_body": ti["body"],
            "tip_liq": ti["liq"], "tip_neg": ti["neg"],
            "is_bill": stype == "Bill",
        })

    auctions = sorted(auctions, key=lambda x: x["date"], reverse=True)[:20]
    avg_btc = sum(btc_list) / len(btc_list) if btc_list else 0

    max_v = max(tbill, note, bond, tips, 0.1)
    def pct(v): return round(v / max_v * 95)
    breakdown = [
        {"label": "T-Bills",      "amt": f"${tbill:.0f}B", "pct": pct(tbill), "color": "#f87171"},
        {"label": "Notes(2~7Y)",  "amt": f"${note:.0f}B",  "pct": pct(note),  "color": "#60a5fa"},
        {"label": "Bonds(10~30Y)","amt": f"${bond:.0f}B",  "pct": pct(bond),  "color": "#fbbf24"},
        {"label": "TIPS",         "amt": f"${tips:.0f}B",  "pct": pct(tips),  "color": "#a78bfa"},
    ]
    schedule = [
        {"label": "Q1: 2026-01-27 완료", "current": False},
        {"label": "Q2: 2026-04-28 예정", "current": True},
        {"label": "Q3: 2026-07-27 예정", "current": False},
        {"label": "Q4: 2026-10-27 예정", "current": False},
    ]
    def fmt_b(v): return f"${v:.0f}B" if v >= 1 else f"${v*1000:.0f}M"
    return {
        "next_qra": "2026-04-28",
        "tbill_30d": fmt_b(tbill), "coupon_30d": fmt_b(note + bond),
        "tips_30d": fmt_b(tips), "total_30d": fmt_b(total),
        "avg_btc": f"{avg_btc:.2f}x" if avg_btc else "—",
        "breakdown": breakdown, "schedule": schedule, "auctions": auctions,
        "start_date": start,
    }


# ──────────────────────────────────────────────
# Cron 갱신 함수 (Neon에 저장)
# ──────────────────────────────────────────────

def next_thursday_kst():
    now = datetime.now(KST)
    days_ahead = (3 - now.weekday()) % 7
    if days_ahead == 0 and now.hour >= 6:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).strftime("%m-%d")


def run_refresh_nl():
    try:
        df, model_info = build_nl_data()
        db_set("nl_summary",   build_nl_summary(df))
        db_set("nl_chart1",    build_chart1(df))
        db_set("nl_chart2",    build_chart2(df))
        db_set("nl_table",     build_nl_table(df))
        db_set("nl_model",     model_info)
        db_set("nl_next_h41",  next_thursday_kst())
        db_set("nl_error",     None)
        print("NL 갱신 완료")
    except Exception as e:
        db_set("nl_error", str(e))
        print(f"NL 오류: {e}")


def run_refresh_tic():
    try:
        pivot = fetch_tic_data()
        db_set("tic_chart",      build_tic_chart(pivot))
        db_set("tic_table",      build_tic_table(pivot))
        db_set("tic_updated_at", pivot.index[-1].strftime("%Y-%m"))
        db_set("tic_error",      None)
        print("TIC 갱신 완료")
    except Exception as e:
        db_set("tic_error", str(e))
        print(f"TIC 오류: {e}")


def run_refresh_dts():
    try:
        dep, wit, bal, date = fetch_dts_data()
        db_set("dts_deposits",    dep)
        db_set("dts_withdrawals", wit)
        db_set("dts_balance",     bal)
        db_set("dts_date",        date)
        db_set("dts_error",       None)
        print(f"DTS 갱신 완료: {date}")
    except Exception as e:
        db_set("dts_error", str(e))
        print(f"DTS 오류: {e}")


def run_refresh_qra():
    try:
        db_set("qra_data",  fetch_qra_data())
        db_set("qra_error", None)
        print("QRA 갱신 완료")
    except Exception as e:
        db_set("qra_error", str(e))
        print(f"QRA 오류: {e}")


# ──────────────────────────────────────────────
# Flask 라우트
# ──────────────────────────────────────────────

@app.route("/")
def index():
    summary      = db_get("nl_summary")
    chart1_html  = db_get("nl_chart1")
    chart2_html  = db_get("nl_chart2")
    table_rows   = db_get("nl_table") or []
    model_info   = db_get("nl_model")
    error        = db_get("nl_error")
    next_h41     = db_get("nl_next_h41") or next_thursday_kst()
    updated_at   = db_get_updated_at("nl_summary") or "—"

    tic_chart_html = db_get("tic_chart")
    tic_table      = db_get("tic_table") or []
    tic_updated_at = db_get("tic_updated_at") or "—"
    tic_error      = db_get("tic_error")

    dts_deposits    = db_get("dts_deposits") or []
    dts_withdrawals = db_get("dts_withdrawals") or []
    dts_balance     = db_get("dts_balance") or []
    dts_date        = db_get("dts_date") or "—"
    dts_error       = db_get("dts_error")

    qra_data  = db_get("qra_data")
    qra_error = db_get("qra_error")

    tic_legend = [{"name": c.replace('"',''), "color": TIC_COLORS.get(c.replace('"',''), "#888")}
                  for c in TIC_COUNTRIES[:6]]

    return render_template_string(HTML_TEMPLATE,
        chart1_html=chart1_html, chart2_html=chart2_html,
        summary=summary, table_rows=table_rows,
        updated_at=updated_at, error=error, model_info=model_info,
        tic_chart_html=tic_chart_html, tic_table=tic_table,
        tic_updated_at=tic_updated_at, tic_error=tic_error,
        tic_legend=tic_legend, next_h41=next_h41,
        dts_deposits=dts_deposits, dts_withdrawals=dts_withdrawals,
        dts_balance=dts_balance, dts_date=dts_date, dts_error=dts_error,
        qra_data=qra_data, qra_error=qra_error,
    )


@app.route("/api/cron/nl")
def cron_nl():
    secret = request.headers.get("Authorization", "")
    if CRON_SECRET and secret != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "unauthorized"}), 401
    run_refresh_nl()
    kst = __import__("datetime").datetime.now(__import__("pytz").timezone("Asia/Seoul"))
    return jsonify({"status": "ok", "updated_at": kst.strftime("%Y-%m-%d %H:%M KST"), "next": "daily 09:00 KST / Wed 16:30 KST"})


@app.route("/api/cron/tic")
def cron_tic():
    secret = request.headers.get("Authorization", "")
    if CRON_SECRET and secret != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "unauthorized"}), 401
    threading.Thread(target=run_refresh_tic, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/cron/dts")
def cron_dts():
    secret = request.headers.get("Authorization", "")
    if CRON_SECRET and secret != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "unauthorized"}), 401
    threading.Thread(target=run_refresh_dts, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/cron/qra")
def cron_qra():
    secret = request.headers.get("Authorization", "")
    if CRON_SECRET and secret != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "unauthorized"}), 401
    threading.Thread(target=run_refresh_qra, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/cron/all")
def cron_all():
    secret = request.headers.get("Authorization", "")
    if CRON_SECRET and secret != f"Bearer {CRON_SECRET}":
        return jsonify({"error": "unauthorized"}), 401
    for fn in [run_refresh_nl, run_refresh_dts, run_refresh_qra]:
        threading.Thread(target=fn, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/health")
def health():
    return "ok"


# ──────────────────────────────────────────────
# DB 초기화 + 첫 데이터 로딩
# ──────────────────────────────────────────────

if DATABASE_URL:
    try:
        init_db()
        # DB에 데이터가 없을 때만 초기 로딩
        if db_get("nl_summary") is None:
            print("초기 데이터 없음 → 백그라운드 로딩 시작")
            for fn in [run_refresh_nl, run_refresh_tic, run_refresh_dts, run_refresh_qra]:
                threading.Thread(target=fn, daemon=True).start()
    except Exception as e:
        print(f"DB 초기화 오류: {e}")
else:
    print("WARNING: DATABASE_URL 환경변수가 설정되지 않았습니다.")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)


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
    /* DTS 섹션 */
    .dts-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;}
    .dts-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px 16px;}
    .dts-hd{font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;display:flex;align-items:center;gap:6px;}
    .dts-dot{width:6px;height:6px;border-radius:50%;display:inline-block;}
    .dts-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);}
    .dts-row:last-child{border-bottom:none;}
    .dts-name{font-size:12px;color:rgba(255,255,255,0.4);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:10px;}
    .dts-amt{font-size:12px;font-weight:500;white-space:nowrap;}
    .c-in{color:#34d399;}.c-out{color:#f87171;}
    /* 캘린더 */
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
    /* QRA 경매 툴팁 - JS 제어 fixed 팝업 */
    #auction-tooltip{display:none;position:fixed;z-index:9999;
      background:#1a1a22;border:1px solid rgba(255,255,255,0.15);border-radius:8px;
      padding:10px 13px;width:230px;pointer-events:none;
      font-size:11px;line-height:1.55;color:rgba(255,255,255,0.45);}
    #auction-tooltip b{color:rgba(255,255,255,0.8);font-weight:500;display:block;margin-bottom:4px;}
    #auction-tooltip .tip-liq{margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.08);font-size:11px;}
    #auction-tooltip .tip-neg{color:#f87171;font-weight:500;}
    #auction-tooltip .tip-neu{color:rgba(255,255,255,0.45);font-weight:500;}
    .has-tip{cursor:default;}
    /* 인라인 탭 (DTS/QRA) */
    .itab-row{display:flex;gap:4px;margin-bottom:10px;}
    .itab{font-size:11px;padding:4px 14px;border:1px solid rgba(255,255,255,0.1);border-radius:20px;background:transparent;cursor:pointer;color:rgba(255,255,255,0.3);transition:all .15s;}
    .itab.active{background:rgba(96,165,250,0.12);border-color:rgba(96,165,250,0.35);color:#60a5fa;}
    .itab-panel{display:none;}.itab-panel.active{display:block;}
    /* QRA 바 차트 */
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
    /* 접기/펼치기 */
    details.collapsible{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;margin-bottom:12px;overflow:hidden;}
    details.collapsible summary{padding:11px 16px;font-size:10px;font-weight:500;color:rgba(255,255,255,0.35);text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;display:flex;align-items:center;gap:8px;list-style:none;user-select:none;}
    details.collapsible summary::-webkit-details-marker{display:none;}
    details.collapsible summary::before{content:'▶';font-size:8px;color:rgba(255,255,255,0.2);transition:transform .2s;flex-shrink:0;}
    details.collapsible[open] summary::before{transform:rotate(90deg);}
    details.collapsible summary:hover{color:rgba(255,255,255,0.6);background:rgba(255,255,255,0.02);}
    .collapsible-body{padding:14px 16px;border-top:1px solid rgba(255,255,255,0.06);}
  </style>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
  <script>
    window.onload=function(){
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
    // 경매 툴팁 (마우스 위치 기반 fixed)
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
  <h1><span class="nav-dot"></span> Fed Dashboard <span class="badge">● Live</span></h1>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <span class="meta" id="cd">Updated: {{ updated_at }}</span>
    <button class="refresh-btn" onclick="manualRefresh()">↻ Refresh</button>
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
    <div class="mc"><div class="mc-lbl">WALCL <span style="font-weight:400;color:#bbb;">주간</span> <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED↗</a></div><div class="mc-val">{{ summary.walcl }}</div><div class="mc-sub neu">{{ summary.walcl_date }} · H.4.1 매주 수요일</div></div>
    <div class="mc"><div class="mc-lbl">TGA <span style="font-weight:400;color:#bbb;">주간</span> <a class="src-link" href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank">FRED↗</a></div><div class="mc-val">{{ summary.tga }}</div><div class="mc-sub neu">{{ summary.tga_date }} · 다음 발표 ~{{ next_h41 }}</div></div>
    <div class="mc"><div class="mc-lbl">RRP <span style="font-weight:400;color:#bbb;">일간</span> <a class="src-link" href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank">FRED↗</a></div><div class="mc-val">{{ summary.rrp }}</div><div class="mc-sub neu">{{ summary.rrp_date }}</div></div>
    <div class="mc"><div class="mc-lbl">S&P 500</div><div class="mc-val">{{ summary.spx_raw }}</div><div class="mc-sub neu">{{ summary.base_date }}</div></div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">WALCL 구성: Net Liquidity · TGA · RRP — Daily (2000–present)
        <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED ↗</a>
      </div>
      <div class="legend">
        <span><span style="width:12px;height:8px;background:rgba(96,165,250,0.6);border-radius:2px;display:inline-block;"></span>Net Liquidity</span>
        <span><span style="width:12px;height:8px;background:rgba(52,211,153,0.55);border-radius:2px;display:inline-block;"></span>TGA</span>
        <span><span style="width:12px;height:8px;background:rgba(251,191,36,0.55);border-radius:2px;display:inline-block;"></span>RRP</span>
        <span style="font-size:10px;color:rgba(255,255,255,0.2);">음영: 경기침체</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c1','in')">+</button><button onclick="zoomChart('c1','out')">−</button><button onclick="resetChart('c1')">↺</button></div>
    </div>
    <div id="c1" style="padding:4px;">{{ chart1_html | safe }}</div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">S&P 500 vs NL Regression FV — Daily (2000–present)
        <a class="src-link" href="https://fred.stlouisfed.org/series/SP500" target="_blank">FRED ↗</a>
      </div>
      <div class="legend">
        <span><span style="width:16px;height:2px;background:#e2e2e2;display:inline-block;"></span>S&P 500</span>
        <span><span style="width:16px;height:2px;border-top:2px dashed #60a5fa;display:inline-block;"></span>NL 회귀 FV</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c2','in')">+</button><button onclick="zoomChart('c2','out')">−</button><button onclick="resetChart('c2')">↺</button></div>
    </div>
    <div id="c2" style="padding:4px;">{{ chart2_html | safe }}</div>
  </div>

  <div class="section-title">TGA 사용처 · DTS · QRA
    <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ dts_date }} 기준</span>
    <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank">fiscaldata ↗</a>
  </div>

  <div id="dts-qra-tabs">
    <div class="itab-row">
      <button class="itab active" id="dts-qra-tabs-tab-dts" onclick="switchItab('dts-qra-tabs','dts')">DTS 일일 내역</button>
      <button class="itab" id="dts-qra-tabs-tab-qra" onclick="switchItab('dts-qra-tabs','qra')">QRA 국채발행</button>
    </div>

    <!-- DTS 패널 -->
    <div class="itab-panel active" id="dts-qra-tabs-panel-dts">
      {% if dts_error %}
      <div class="error" style="font-size:12px;">DTS 데이터 오류: {{ dts_error }}</div>
      {% elif not dts_deposits %}
      <div class="loading" style="padding:20px;">DTS 데이터 로딩 중...</div>
      {% else %}
      <div class="dts-grid">
        <div class="dts-card">
          <div class="dts-hd"><span class="dts-dot" style="background:#34d399;"></span>주요 입금 항목 (Table II)
            <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/deposits-withdrawals-operating-cash" target="_blank">↗</a>
          </div>
          {% for item in dts_deposits %}
          <div class="dts-row">
            <span class="dts-name">{{ item.name }}</span>
            <span class="dts-amt c-in">+{{ item.amt }}</span>
          </div>
          {% endfor %}
        </div>
        <div class="dts-card">
          <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>주요 출금 항목 (Table II)
            <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/deposits-withdrawals-operating-cash" target="_blank">↗</a>
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
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>TGA 당일 순변동 요약
          <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/operating-cash-balance" target="_blank">↗</a>
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

    <!-- QRA 패널 -->
    <div class="itab-panel" id="dts-qra-tabs-panel-qra">
      {% if qra_error %}
      <div class="error" style="font-size:12px;">QRA 데이터 오류: {{ qra_error }}</div>
      {% elif not qra_data %}
      <div class="loading" style="padding:20px;">QRA 데이터 로딩 중...</div>
      {% else %}
      <!-- 메트릭 카드 -->
      <div class="metrics" style="margin-bottom:10px;">
        <div class="mc"><div class="mc-lbl">다음 QRA 발표</div><div class="mc-val" style="font-size:16px;">{{ qra_data.next_qra }}</div><div class="mc-sub neu">분기 차입 수요 발표</div></div>
        <div class="mc"><div class="mc-lbl">최근 T-Bill 발행 (30일)</div><div class="mc-val">{{ qra_data.tbill_30d }}</div><div class="mc-sub neg">유동성 흡수↓</div></div>
        <div class="mc"><div class="mc-lbl">최근 쿠폰채 발행 (30일)</div><div class="mc-val">{{ qra_data.coupon_30d }}</div><div class="mc-sub neg">NL 압박↓</div></div>
        <div class="mc"><div class="mc-lbl">최근 TIPS 발행 (30일)</div><div class="mc-val">{{ qra_data.tips_30d }}</div><div class="mc-sub neu">물가연동</div></div>
        <div class="mc"><div class="mc-lbl">평균 응찰률 (BTC)</div><div class="mc-val">{{ qra_data.avg_btc }}</div><div class="mc-sub neu">최근 30일 평균</div></div>
        <div class="mc"><div class="mc-lbl">총 발행 (30일)</div><div class="mc-val">{{ qra_data.total_30d }}</div><div class="mc-sub neg">시장 흡수 규모↓</div></div>
      </div>

      <!-- 발행 구성 바 -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>국채 발행 구성 (최근 30일 · 유동성 흡수)
          <a class="src-link" href="https://www.treasurydirect.gov/TA_WS/securities/auctioned" target="_blank">TreasuryDirect ↗</a>
        </div>
        {% for item in qra_data.breakdown %}
        <div class="qra-bar-row">
          <span class="qra-bar-label">{{ item.label }}</span>
          <div class="qra-bar-bg"><div class="qra-bar-fill" style="width:{{ item.pct }}%;background:{{ item.color }};"></div></div>
          <span class="qra-bar-amt">{{ item.amt }}</span>
        </div>
        {% endfor %}
        <div style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:8px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);">
          * 국채 발행 → TGA 유입 → NL 감소. T-Bill 위주 발행 시 MMF(RRP)↓ 상쇄 효과 있음.
        </div>
      </div>

      <!-- QRA 판독 기준 -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>QRA 유동성 판독 기준</div>
        <div class="dts-row"><span class="dts-name">T-Bill 비중 높음</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">RRP↓ 상쇄</span><span class="qra-tag tag-in">NL 중립~유입</span></div>
        <div class="dts-row"><span class="dts-name">쿠폰채 비중 높음</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">은행 준비금 흡수</span><span class="qra-tag tag-out">NL 압박</span></div>
        <div class="dts-row"><span class="dts-name">차입 규모 예상↑</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA 급증 예고</span><span class="qra-tag tag-out">NL 하락 신호</span></div>
        <div class="dts-row"><span class="dts-name">차입 규모 예상↓</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA 완만 유지</span><span class="qra-tag tag-in">NL 안정 신호</span></div>
        <div class="dts-row"><span class="dts-name">부채한도 협상 중</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA 소진 지속</span><span class="qra-tag tag-in">NL 인위적 상승</span></div>
        <div class="dts-row"><span class="dts-name">부채한도 해소 후</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA 재충전</span><span class="qra-tag tag-out">NL 급락 위험</span></div>
      </div>

      <!-- 발표 일정 -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#fbbf24;"></span>QRA 발표 일정 (2026)</div>
        <div class="qra-pill-row">
          {% for q in qra_data.schedule %}
          <span class="qra-pill {{ 'hl' if q.current else '' }}">{{ q.label }}</span>
          {% endfor %}
        </div>
        <div style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:8px;">
          TBAC 발표 당일 시장 변동성 주의. 차입 규모↑ → 금리↑ · NL↓ 압력.
        </div>
      </div>

      <!-- 최근 경매 내역 -->
      <div class="section-title" style="margin-top:4px;">최근 경매 내역 (30일)
        <a class="src-link" href="https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&dateFieldName=auctionDate&startDate={{ qra_data.start_date }}" target="_blank">원본 ↗</a>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th style="text-align:left;">경매일</th>
            <th style="text-align:left;">종류</th>
            <th style="text-align:left;">만기</th>
            <th>발행액(B)</th>
            <th>응찰률</th>
            <th>금리/할인율</th>
          </tr></thead>
          <tbody>
            {% for r in qra_data.auctions %}
            <tr>
              <td style="text-align:left;">{{ r.date }}</td>
              <td style="text-align:left;">
                <span class="has-tip" style="font-size:11px;padding:1px 7px;border-radius:4px;background:{{ r.type_bg }};color:{{ r.type_color }};"
                  data-tip-title="{{ r.tip_title }} · {{ r.term }}"
                  data-tip-body="{{ r.tip_body }}"
                  data-tip-liq="{{ r.tip_liq }}"
                  data-tip-neg="{{ 'true' if r.tip_neg else 'false' }}">{{ r.stype }}</span>
              </td>
              <td style="text-align:left;color:rgba(255,255,255,0.4);">{{ r.term }}</td>
              <td>{{ r.amt }}</td>
              <td>
                <span class="{{ 'badge-up' if r.btc_ok else 'badge-dn' }} has-tip"
                  data-tip-title="응찰률 (Bid-to-Cover)"
                  data-tip-body="경쟁 입찰 제출액 ÷ 낙찰액. 수요 강도 지표."
                  data-tip-liq="{{ '2.3x↑ 수요 양호' if r.btc_ok else '2.3x↓ 수요 부족 경고' }}"
                  data-tip-neg="{{ 'false' if r.btc_ok else 'true' }}">{{ r.btc }}</span>
              </td>
              <td>
                <span class="has-tip" style="color:rgba(255,255,255,0.5);"
                  data-tip-title="낙찰 금리/할인율"
                  data-tip-body="{{ 'T-Bill: 할인율(Discount Rate) 기준. 높을수록 단기 자금 비용↑.' if r.is_bill else '쿠폰채: 최고 낙찰 수익률(High Yield). 높을수록 재정 이자 부담↑ · NL 장기 압박.' }}"
                  data-tip-liq="{{ '단기금리 방향성 지표' if r.is_bill else '장기금리↑ → 주식 멀티플 압박' }}"
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

  <div class="section-title">재정 이벤트 캘린더
    <a class="src-link" href="https://www.irs.gov/businesses/small-businesses-self-employed/tax-calendar" target="_blank">IRS Calendar ↗</a>
  </div>
  <div class="chart-card" style="padding:14px 16px;margin-bottom:12px;">
    <div class="cal-legend">
      <span><span class="cal-legend-dot" style="background:#34d399;"></span>유동성 유입 (환급·정부지출)</span>
      <span><span class="cal-legend-dot" style="background:#f87171;"></span>유동성 유출 (세금납부·국채발행)</span>
      <span><span class="cal-legend-dot" style="background:rgba(255,255,255,0.2);"></span>중립/발표</span>
    </div>
    <div class="cal-grid">
      <div class="cal-m"><div class="cal-mn">1월</div>
        <span class="cal-ev ev-out">4Q 추정세 납부 (1/15)</span>
        <span class="cal-ev ev-neu">IRS 신고시즌 개시</span>
        <span class="cal-ev ev-in">사회보장·메디케어↑</span>
        <span class="cal-ev ev-neu">QRA 발표(~1/29)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">2월</div>
        <span class="cal-ev ev-in">환급 피크 (W-2)↑↑</span>
        <span class="cal-ev ev-in">EITC·CTC 환급 개시</span>
        <span class="cal-ev ev-in">사회보장·메디케어↑</span>
        <span class="cal-ev ev-neu">H.4.1 매주 수요일</span>
      </div>
      <div class="cal-m"><div class="cal-mn">3월</div>
        <span class="cal-ev ev-in">환급 지속↑</span>
        <span class="cal-ev ev-neu">S-Corp·파트너십 신고(3/15)</span>
        <span class="cal-ev ev-neu">T-Note 분기발행</span>
        <span class="cal-ev ev-out">국채 만기·롤오버↓</span>
      </div>
      <div class="cal-m hl-red"><div class="cal-mn red">4월 ★</div>
        <span class="cal-ev ev-out">Tax Day (4/15)↓↓</span>
        <span class="cal-ev ev-out">1Q 추정세 (4/15)↓</span>
        <span class="cal-ev ev-out">TGA 급증 → NL 감소</span>
        <span class="cal-ev ev-neu">연장신청(Form 4868)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">5월</div>
        <span class="cal-ev ev-in">잔여 환급 지속↑</span>
        <span class="cal-ev ev-neu">Form 990 비영리 신고</span>
        <span class="cal-ev ev-in">정부 지출 정상화↑</span>
        <span class="cal-ev ev-neu">QRA 발표(~4월말)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">6월</div>
        <span class="cal-ev ev-out">2Q 추정세 (6/15)↓</span>
        <span class="cal-ev ev-in">국방·인프라 지출↑</span>
        <span class="cal-ev ev-neu">T-Bill 정기 롤오버</span>
        <span class="cal-ev ev-neu">FOMC 회의(통상)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">7월</div>
        <span class="cal-ev ev-in">사회보장 지급↑</span>
        <span class="cal-ev ev-in">메디케어·메디케이드↑</span>
        <span class="cal-ev ev-in">여름 인프라 지출↑</span>
        <span class="cal-ev ev-neu">QRA 발표(~7/28)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">8월</div>
        <span class="cal-ev ev-out">T-Bill 대규모 발행↓</span>
        <span class="cal-ev ev-neu">QRA·TBAC 발표</span>
        <span class="cal-ev ev-in">정부 재량지출↑</span>
        <span class="cal-ev ev-neu">잭슨홀 연설(연준)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">9월</div>
        <span class="cal-ev ev-out">3Q 추정세 (9/15)↓</span>
        <span class="cal-ev ev-in">회계연도 마감 지출↑↑</span>
        <span class="cal-ev ev-out">국채 분기 발행↓</span>
        <span class="cal-ev ev-neu">회계연도 종료(9/30)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">10월</div>
        <span class="cal-ev ev-neu">새 회계연도 개시(FY)</span>
        <span class="cal-ev ev-neu">연장 마감(10/15)</span>
        <span class="cal-ev ev-in">사회보장 COLA 인상↑</span>
        <span class="cal-ev ev-neu">TIC 데이터 발표(~18일)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">11월</div>
        <span class="cal-ev ev-in">연말 정부 지출↑</span>
        <span class="cal-ev ev-in">사회보장·복지지출↑</span>
        <span class="cal-ev ev-neu">QRA 발표(~10월말)</span>
        <span class="cal-ev ev-neu">T-Bond 분기발행</span>
      </div>
      <div class="cal-m hl-green"><div class="cal-mn green">12월 ★</div>
        <span class="cal-ev ev-in">지출 피크↑↑ (회계마감)</span>
        <span class="cal-ev ev-out">연말 세금 납부↓</span>
        <span class="cal-ev ev-in">사회보장 선지급↑</span>
        <span class="cal-ev ev-neu">연준 최종 FOMC</span>
      </div>
    </div>
  </div>

  <details class="collapsible">
    <summary>시장 유동성 기준 <a class="src-link" href="https://www.federalreserve.gov/releases/h41/" target="_blank" onclick="event.stopPropagation()">H.4.1 ↗</a></summary>
    <div class="collapsible-body">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:12px;line-height:1.8;">
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">📥 유동성 유입 신호 (NL 상승 조건)</div>
          <div class="dts-row"><span class="dts-name">WALCL 증가</span><span style="color:#34d399;font-size:11px;">Fed 자산 매입 → 시중 자금↑</span></div>
          <div class="dts-row"><span class="dts-name">TGA 감소</span><span style="color:#34d399;font-size:11px;">재무부 지출 → 은행 준비금↑</span></div>
          <div class="dts-row"><span class="dts-name">RRP 감소</span><span style="color:#34d399;font-size:11px;">MMF 자금 시장 유입↑</span></div>
          <div class="dts-row"><span class="dts-name">부채한도 협상</span><span style="color:#34d399;font-size:11px;">TGA 소진 → NL 급상승</span></div>
          <div class="dts-row"><span class="dts-name">QE 재개</span><span style="color:#34d399;font-size:11px;">WALCL 확대 → 직접 유동성↑</span></div>
          <div class="dts-row"><span class="dts-name">환급 시즌 (2~3월)</span><span style="color:#34d399;font-size:11px;">TGA 감소·소비↑</span></div>
          <div class="dts-row"><span class="dts-name">SRF·정책 대출</span><span style="color:#34d399;font-size:11px;">Fed 긴급 유동성 공급↑</span></div>
          <div class="dts-row"><span class="dts-name">외환보유 달러 환류</span><span style="color:#34d399;font-size:11px;">해외 중앙은행 스왑라인↑</span></div>
        </div>
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">📤 유동성 유출 신호 (NL 하락 조건)</div>
          <div class="dts-row"><span class="dts-name">WALCL 감소 (QT)</span><span style="color:#f87171;font-size:11px;">Fed 자산 축소 → 준비금 감소↓</span></div>
          <div class="dts-row"><span class="dts-name">TGA 급증</span><span style="color:#f87171;font-size:11px;">세금납부·국채발행 → 시중 흡수↓</span></div>
          <div class="dts-row"><span class="dts-name">RRP 증가</span><span style="color:#f87171;font-size:11px;">MMF가 Fed에 자금 예치↓</span></div>
          <div class="dts-row"><span class="dts-name">Tax Day (4월)</span><span style="color:#f87171;font-size:11px;">TGA 급증 → NL 단기 압박↓</span></div>
          <div class="dts-row"><span class="dts-name">추정세 납부(분기)</span><span style="color:#f87171;font-size:11px;">1/15 · 4/15 · 6/15 · 9/15↓</span></div>
          <div class="dts-row"><span class="dts-name">T-Bill 대규모 발행</span><span style="color:#f87171;font-size:11px;">시중 자금 국채로 흡수↓</span></div>
          <div class="dts-row"><span class="dts-name">부채한도 해소 후</span><span style="color:#f87171;font-size:11px;">TGA 재충전 → NL 급락↓</span></div>
          <div class="dts-row"><span class="dts-name">기준금리 인상</span><span style="color:#f87171;font-size:11px;">RRP 금리 매력↑ → 자금유출↓</span></div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:rgba(255,255,255,0.25);">
        💡 <b style="color:rgba(255,255,255,0.4);">핵심 공식:</b> NL = WALCL − TGA − RRP &nbsp;·&nbsp;
        NL이 상승하면 시중 유동성 증가 → 위험자산 선호 경향 &nbsp;·&nbsp;
        <a href="https://fred.stlouisfed.org/series/WALCL" target="_blank" style="color:#60a5fa;text-decoration:none;">WALCL↗</a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank" style="color:#60a5fa;text-decoration:none;">TGA↗</a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank" style="color:#60a5fa;text-decoration:none;">RRP↗</a>
      </div>
    </div>
  </details>

  <details class="collapsible">
    <summary>계산 방법론</summary>
    <div class="collapsible-body">
      <div class="method-box" style="margin-bottom:0;">
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
    </div>
  </details>

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
          <td>{% if row.dod_pos is none %}<span style="color:rgba(255,255,255,0.3);font-size:11px;">{{ row.dod }}</span>{% elif row.dod_pos %}<span class="badge-up">{{ row.dod }}</span>{% else %}<span class="badge-dn">{{ row.dod }}</span>{% endif %}</td>
          <td>{{ row.spx }}</td><td>{{ row.fv_nl }}</td>
          <td>{% if row.gap is not none %}<span class="{{ 'badge-dn' if row.gap_pos else 'badge-up' }}">{{ row.gap }}</span>{% else %}—{% endif %}</td>
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
        <div class="chart-title">주요국 미국채 보유량 — Monthly (2000–present)
          <a class="src-link" href="https://home.treasury.gov/data/treasury-international-capital-tic-system" target="_blank">TIC ↗</a>
        </div>
        <div class="legend">
          {% for c in tic_legend %}
          <span><span style="width:16px;height:2px;background:{{ c.color }};display:inline-block;"></span>{{ c.name }}</span>
          {% endfor %}
        </div>
      </div>
      <div class="zoom-btns"><button onclick="zoomChart('ctic','in')">+</button><button onclick="zoomChart('ctic','out')">−</button><button onclick="resetChart('ctic')">↺</button></div>
    </div>
    <div id="ctic" style="padding:4px;">{{ tic_chart_html | safe }}</div>
  </div>

  <div class="section-title">최신 보유량 순위 <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ tic_updated_at }} 기준 · 약 6주 후행 발표</span></div>
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
    Net Liquidity: <a href="https://fred.stlouisfed.org" target="_blank" style="color:#60a5fa;text-decoration:none;">FRED</a> (WALCL·WDTGAL·RRPONTSYD·SP500) &nbsp;|&nbsp;
    TGA 사용처: <a href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank" style="color:#60a5fa;text-decoration:none;">fiscaldata.treasury.gov</a> &nbsp;|&nbsp;
    국가별 미국채: <a href="https://home.treasury.gov/data/treasury-international-capital-tic-system" target="_blank" style="color:#60a5fa;text-decoration:none;">U.S. Treasury TIC</a> &nbsp;|&nbsp; 2000–present
  </div>
</div>
</body>
</html>
"""

