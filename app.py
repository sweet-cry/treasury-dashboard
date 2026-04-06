"""
Net Liquidity + ???癰?沃섎㈇?낉㎖?癰귣똻? Dashboard
=============================================
Vercel + Neon(PostgreSQL) 甕곌쑴??
??띻펾癰궰??
  FRED_API_KEY  : FRED API Key
  DATABASE_URL  : Neon PostgreSQL ?怨뚭퍙 ?얜챷???  START_DATE    : ??뽰삂??(疫꿸퀡??2000-01-01)
  CRON_SECRET   : Cron ?遺얜굡?????癰귣똾?????쀪쾿????
??낅쑓??꾨뱜 ???餓?(vercel.json cron):
  - NL/DTS/QRA : 筌띲끉??00:30 UTC
  - TIC        : 筌띲끉??18??02:00 UTC
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


# ????????????????????????????????????????????????????????????????????????????????????????????
# Neon DB ?醫뤿뼢
# ????????????????????????????????????????????????????????????????????????????????????????????

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """筌?Ŋ?????뵠???λ뜃由??(筌ㅼ뮇??1??"""
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


# ????????????????????????????????????????????????????????????????????????????????????????????
# FRED ?怨쀬뵠??fetch
# ????????????????????????????????????????????????????????????????????????????????????????????

def fetch_series(series_id, start, frequency="d"):
    if not API_KEY:
        raise ValueError("FRED_API_KEY ??띻펾癰궰??? ??쇱젟??? ??녿릭??щ빍??")
    params = dict(series_id=series_id, api_key=API_KEY, file_type="json",
                  observation_start=start, frequency=frequency)
    r = req.get(FRED_BASE, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error_message" in data:
        raise ValueError(f"{series_id}: {data['error_message']}")
    obs = [(o["date"], float(o["value"])) for o in data["observations"] if o["value"] != "."]
    if not obs:
        raise ValueError(f"{series_id}: ?怨쀬뵠????곸벉")
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
    raise ValueError(f"{series_id}: ????揶쎛?館釉?frequency ??곸벉")


# ????????????????????????????????????????????????????????????????????????????????????????????
# NL ?④쑴沅?# ????????????????????????????????????????????????????????????????????????????????????????????

def fmt_val(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "??
    if v != v:
        return "??
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

    # yfinance fallback: FRED보다 최신 데이터 보완
    try:
        import yfinance as yf
        yf_spx = yf.download("^GSPC", start=START_DATE, progress=False, auto_adjust=True)["Close"]
        yf_spx.index = pd.to_datetime(yf_spx.index).tz_localize(None)
        yf_spx.name = "SP500"
        missing = yf_spx.index.difference(spx_d.index)
        if len(missing) > 0:
            spx_d = pd.concat([spx_d, yf_spx.loc[missing]]).sort_index()
    except Exception:
        pass

    # yfinance fallback: FRED蹂대떎 理쒖떊 ?곗씠??蹂댁셿
    try:
        import yfinance as yf
        yf_spx = yf.download("^GSPC", start=START_DATE, progress=False, auto_adjust=True)["Close"]
        yf_spx.index = pd.to_datetime(yf_spx.index).tz_localize(None)
        yf_spx.name = "SP500"
        missing = yf_spx.index.difference(spx_d.index)
        if len(missing) > 0:
            spx_d = pd.concat([spx_d, yf_spx.loc[missing]]).sort_index()
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
        fv_nl_gap = f"{'+' if gap>0 else ''}{gap:.1f}% {'?⑥쥚猷듿첎?' if gap>0 else '?????'}"
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
        "fv_nl_gap": fv_nl_gap or "?怨쀬뵠???봔鈺?, "fv_nl_cheap": fv_nl_cheap,
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
            "dod": f"{'?? if dod>0 else ('?? if dod<0 else '??')}{abs(round(dod)):,.0f}" if dod is not None else "??,
            "dod_pos": None if dod is None or round(dod)==0 else dod > 0,
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
        {"month": 2, "label": "??랁닋 ??녠쾿", "color": "rgba(52,211,153,0.5)"},
        {"month": 3, "label": "??랁닋 ??녠쾿", "color": "rgba(52,211,153,0.5)"},
        {"month": 4, "label": "Tax Day",   "color": "rgba(248,113,113,0.6)"},
        {"month": 6, "label": "2Q ?곕뗄???, "color": "rgba(251,191,36,0.5)"},
        {"month": 9, "label": "3Q ?곕뗄???, "color": "rgba(251,191,36,0.5)"},
        {"month": 1, "label": "4Q ?곕뗄???, "color": "rgba(251,191,36,0.5)"},
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
            name="NL ??? FV", line=dict(color="#60a5fa", width=1.5, dash="dot")))
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


# ????????????????????????????????????????????????????????????????????????????????????????????
# TIC
# ????????????????????????????????????????????????????????????????????????????????????????????

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
        raise ValueError("TIC ?怨쀬뵠?????뼓 ??쎈솭")
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
            "chg": f"{'+' if chg and chg>=0 else ''}{chg:.1f}" if chg is not None else "??,
            "chg_pos": chg >= 0 if chg is not None else True,
            "pct": f"{pct:.1f}", "bar_pct": bar_pct,
        })
    return rows[:15]


# ????????????????????????????????????????????????????????????????????????????????????????????
# DTS
# ????????????????????????????????????????????????????????????????????????????????????????????

def fmt_mil(v):
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
        raise ValueError("DTS Table II ?怨쀬뵠????곸벉")

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
        {"name": "????껎닊 (Total Deposits)",    "amt": fmt_mil(total_dep), "pos": True},
        {"name": "???곗뮄??(Total Withdrawals)", "amt": fmt_mil(total_wit), "pos": False},
        {"name": f"?諭???????({'?醫롮뿯' if net>=0 else '?醫롰뀱'})", "amt": fmt_mil(abs(net)), "pos": net >= 0},
    ]
    return dep_list, wit_list, balance_list, latest_date


# ????????????????????????????????????????????????????????????????????????????????????????????
# QRA
# ????????????????????????????????????????????????????????????????????????????????????????????

TIP_INFO = {
    "Bill": {"title": "Treasury Bill", "body": "筌띾슡由?1????꾨릭 ??ｋ┛ ??肄? MMF揶쎛 雅뚯눘??筌띲끉?????T-Bill 獄쏆뮉六????RRP???怨몃뇵 ??NL ?겸뫕爰???쀫립.", "liq": "NL ?怨밸샨 ??쀫립 (RRP ?怨몃뇵)", "neg": False},
    "Note": {"title": "Treasury Note (2~10Y)", "body": "餓λ쵌由???肄? ????猷밸염疫꿸퀗??筌띲끉????餓Β??쑨??筌욊낯????る땾 ??NL ??롮뵭 ?類ｌ젾.", "liq": "????餓Β??쑨????る땾 ??NL??, "neg": True},
    "Bond": {"title": "Treasury Bond (20~30Y)", "body": "?觀由???肄? ????됱뵠???誘る툡 ?觀由?疫뀀뜄??沃섏눊而?", "liq": "?觀由경묾?댿봺 野껋럥以덃에?揶쏄쑴??NL ?類ｌ뺏", "neg": True},
    "TIPS": {"title": "TIPS (?얠눊??怨뺣짗)", "body": "?癒?닊??CPI???怨뺣짗. ??쇱춳疫뀀뜄??筌왖??", "liq": "??쇱춳疫뀀뜄??筌왖????筌욊낯????ｋ궢 ??쀫립??, "neg": False},
    "FRN":  {"title": "FRN (癰궰??놃닊?귐딆퐟)", "body": "13雅?T-Bill 疫뀀뜄????怨뺣짗. ??ｋ┛?얠눘肉?揶쎛繹먮슣???醫딅짗???諭苑?", "liq": "??ｋ┛???醫롪텢 ??NL ?怨밸샨 ??쀫립??, "neg": False},
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
        raise ValueError("QRA 野껋럥???怨쀬뵠????곸벉")

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
            "amt": f"{amt:.1f}", "btc": f"{btc:.2f}x" if btc > 0 else "??,
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
        {"label": "Q1: 2026-01-27 ?袁⑥┷", "current": False},
        {"label": "Q2: 2026-04-28 ??됱젟", "current": True},
        {"label": "Q3: 2026-07-27 ??됱젟", "current": False},
        {"label": "Q4: 2026-10-27 ??됱젟", "current": False},
    ]
    def fmt_b(v): return f"${v:.0f}B" if v >= 1 else f"${v*1000:.0f}M"
    return {
        "next_qra": "2026-04-28",
        "tbill_30d": fmt_b(tbill), "coupon_30d": fmt_b(note + bond),
        "tips_30d": fmt_b(tips), "total_30d": fmt_b(total),
        "avg_btc": f"{avg_btc:.2f}x" if avg_btc else "??,
        "breakdown": breakdown, "schedule": schedule, "auctions": auctions,
        "start_date": start,
    }


# ????????????????????????????????????????????????????????????????????????????????????????????
# Cron 揶쏄퉮????λ땾 (Neon??????
# ????????????????????????????????????????????????????????????????????????????????????????????

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
        print("NL 揶쏄퉮???袁⑥┷")
    except Exception as e:
        db_set("nl_error", str(e))
        print(f"NL ??살첒: {e}")


def run_refresh_tic():
    try:
        pivot = fetch_tic_data()
        db_set("tic_chart",      build_tic_chart(pivot))
        db_set("tic_table",      build_tic_table(pivot))
        db_set("tic_updated_at", pivot.index[-1].strftime("%Y-%m"))
        db_set("tic_error",      None)
        print("TIC 揶쏄퉮???袁⑥┷")
    except Exception as e:
        db_set("tic_error", str(e))
        print(f"TIC ??살첒: {e}")


def run_refresh_dts():
    try:
        dep, wit, bal, date = fetch_dts_data()
        db_set("dts_deposits",    dep)
        db_set("dts_withdrawals", wit)
        db_set("dts_balance",     bal)
        db_set("dts_date",        date)
        db_set("dts_error",       None)
        print(f"DTS 揶쏄퉮???袁⑥┷: {date}")
    except Exception as e:
        db_set("dts_error", str(e))
        print(f"DTS ??살첒: {e}")


def run_refresh_qra():
    try:
        db_set("qra_data",  fetch_qra_data())
        db_set("qra_error", None)
        print("QRA 揶쏄퉮???袁⑥┷")
    except Exception as e:
        db_set("qra_error", str(e))
        print(f"QRA ??살첒: {e}")


# ????????????????????????????????????????????????????????????????????????????????????????????
# Flask ??깆뒭??# ????????????????????????????????????????????????????????????????????????????????????????????

@app.route("/")
def index():
    summary      = db_get("nl_summary")
    chart1_html  = db_get("nl_chart1")
    chart2_html  = db_get("nl_chart2")
    table_rows   = db_get("nl_table") or []
    model_info   = db_get("nl_model")
    error        = db_get("nl_error")
    next_h41     = db_get("nl_next_h41") or next_thursday_kst()
    updated_at   = db_get_updated_at("nl_summary") or "??

    tic_chart_html = db_get("tic_chart")
    tic_table      = db_get("tic_table") or []
    tic_updated_at = db_get("tic_updated_at") or "??
    tic_error      = db_get("tic_error")

    dts_deposits    = db_get("dts_deposits") or []
    dts_withdrawals = db_get("dts_withdrawals") or []
    dts_balance     = db_get("dts_balance") or []
    dts_date        = db_get("dts_date") or "??
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
    threading.Thread(target=run_refresh_nl, daemon=True).start()
    return jsonify({"status": "started"})


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


# ????????????????????????????????????????????????????????????????????????????????????????????
# DB ?λ뜃由??+ 筌??怨쀬뵠??嚥≪뮆逾?# ????????????????????????????????????????????????????????????????????????????????????????????

if DATABASE_URL:
    try:
        init_db()
        # DB???怨쀬뵠?怨? ??곸뱽 ???춸 ?λ뜃由?嚥≪뮆逾?        if db_get("nl_summary") is None:
            print("?λ뜃由??怨쀬뵠????곸벉 ??獄쏄퉫???깆뒲??嚥≪뮆逾???뽰삂")
            for fn in [run_refresh_nl, run_refresh_tic, run_refresh_dts, run_refresh_qra]:
                threading.Thread(target=fn, daemon=True).start()
    except Exception as e:
        print(f"DB ?λ뜃由????살첒: {e}")
else:
    print("WARNING: DATABASE_URL ??띻펾癰궰??? ??쇱젟??? ??녿릭??щ빍??")

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
    /* DTS ?諭??*/
    .dts-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;}
    .dts-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px 16px;}
    .dts-hd{font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;display:flex;align-items:center;gap:6px;}
    .dts-dot{width:6px;height:6px;border-radius:50%;display:inline-block;}
    .dts-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);}
    .dts-row:last-child{border-bottom:none;}
    .dts-name{font-size:12px;color:rgba(255,255,255,0.4);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:10px;}
    .dts-amt{font-size:12px;font-weight:500;white-space:nowrap;}
    .c-in{color:#34d399;}.c-out{color:#f87171;}
    /* 筌?꼶???*/
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
    /* QRA 野껋럥????꾨샍 - JS ??뽯선 fixed ??밸씜 */
    #auction-tooltip{display:none;position:fixed;z-index:9999;
      background:#1a1a22;border:1px solid rgba(255,255,255,0.15);border-radius:8px;
      padding:10px 13px;width:230px;pointer-events:none;
      font-size:11px;line-height:1.55;color:rgba(255,255,255,0.45);}
    #auction-tooltip b{color:rgba(255,255,255,0.8);font-weight:500;display:block;margin-bottom:4px;}
    #auction-tooltip .tip-liq{margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.08);font-size:11px;}
    #auction-tooltip .tip-neg{color:#f87171;font-weight:500;}
    #auction-tooltip .tip-neu{color:rgba(255,255,255,0.45);font-weight:500;}
    .has-tip{cursor:default;}
    /* ?紐껋뵬????(DTS/QRA) */
    .itab-row{display:flex;gap:4px;margin-bottom:10px;}
    .itab{font-size:11px;padding:4px 14px;border:1px solid rgba(255,255,255,0.1);border-radius:20px;background:transparent;cursor:pointer;color:rgba(255,255,255,0.3);transition:all .15s;}
    .itab.active{background:rgba(96,165,250,0.12);border-color:rgba(96,165,250,0.35);color:#60a5fa;}
    .itab-panel{display:none;}.itab-panel.active{display:block;}
    /* QRA 獄?筌△뫂??*/
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
    /* ?臾롫┛/??깊뒄疫?*/
    details.collapsible{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;margin-bottom:12px;overflow:hidden;}
    details.collapsible summary{padding:11px 16px;font-size:10px;font-weight:500;color:rgba(255,255,255,0.35);text-transform:uppercase;letter-spacing:0.08em;cursor:pointer;display:flex;align-items:center;gap:8px;list-style:none;user-select:none;}
    details.collapsible summary::-webkit-details-marker{display:none;}
    details.collapsible summary::before{content:'??;font-size:8px;color:rgba(255,255,255,0.2);transition:transform .2s;flex-shrink:0;}
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
      document.getElementById('cd').textContent='揶쏄퉮??餓?..';
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
    // 野껋럥????꾨샍 (筌띾뜆????袁⑺뒄 疫꿸퀡而?fixed)
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
  <div class="tab" id="tab-btn-tic" onclick="switchTab('tic')">???癰?沃섎㈇?낉㎖?癰귣똻?</div>
</div>

<div id="tab-nl" class="tab-content active">
{% if error %}
  <div class="error">Error: {{ error }}</div>
{% elif not summary %}
  <div class="loading">FRED ?怨쀬뵠??嚥≪뮆逾?餓?.. ?醫롫뻻 ???癒?짗 ??덉쨮?⑥쥙臾??몃빍??</div>
{% else %}

  <div class="metrics">
    <div class="mc"><div class="mc-lbl">Net Liquidity</div><div class="mc-val">{{ summary.nl }}</div><div class="mc-sub {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_chg }}</div></div>
    <div class="mc"><div class="mc-lbl">NL Regression FV</div><div class="mc-val">{{ summary.fv_nl }}</div><div class="mc-sub {{ 'pos' if summary.fv_nl_cheap else ('neg' if summary.fv_nl_cheap is not none else 'neu') }}">{{ summary.fv_nl_gap }}</div></div>
    <div class="mc"><div class="mc-lbl">WALCL <span style="font-weight:400;color:#bbb;">雅뚯눊而?/span> <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.walcl }}</div><div class="mc-sub neu">{{ summary.walcl_date }} 夷?H.4.1 筌띲끉竊???륁뒄??/div></div>
    <div class="mc"><div class="mc-lbl">TGA <span style="font-weight:400;color:#bbb;">雅뚯눊而?/span> <a class="src-link" href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.tga }}</div><div class="mc-sub neu">{{ summary.tga_date }} 夷???쇱벉 獄쏆뮉紐?~{{ next_h41 }}</div></div>
    <div class="mc"><div class="mc-lbl">RRP <span style="font-weight:400;color:#bbb;">??⑥퍢</span> <a class="src-link" href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.rrp }}</div><div class="mc-sub neu">{{ summary.rrp_date }}</div></div>
    <div class="mc"><div class="mc-lbl">S&P 500</div><div class="mc-val">{{ summary.spx_raw }}</div><div class="mc-sub neu">{{ summary.base_date }}</div></div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">WALCL ?닌딄쉐: Net Liquidity 夷?TGA 夷?RRP ??Daily (2000?諭럕esent)
        <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED ??/a>
      </div>
      <div class="legend">
        <span><span style="width:12px;height:8px;background:rgba(96,165,250,0.6);border-radius:2px;display:inline-block;"></span>Net Liquidity</span>
        <span><span style="width:12px;height:8px;background:rgba(52,211,153,0.55);border-radius:2px;display:inline-block;"></span>TGA</span>
        <span><span style="width:12px;height:8px;background:rgba(251,191,36,0.55);border-radius:2px;display:inline-block;"></span>RRP</span>
        <span style="font-size:10px;color:rgba(255,255,255,0.2);">???겫: 野껋럡由곁㎉?κ퍥</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c1','in')">+</button><button onclick="zoomChart('c1','out')">??/button><button onclick="resetChart('c1')">??/button></div>
    </div>
    <div id="c1" style="padding:4px;">{{ chart1_html | safe }}</div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">S&P 500 vs NL Regression FV ??Daily (2000?諭럕esent)
        <a class="src-link" href="https://fred.stlouisfed.org/series/SP500" target="_blank">FRED ??/a>
      </div>
      <div class="legend">
        <span><span style="width:16px;height:2px;background:#e2e2e2;display:inline-block;"></span>S&P 500</span>
        <span><span style="width:16px;height:2px;border-top:2px dashed #60a5fa;display:inline-block;"></span>NL ??? FV</span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c2','in')">+</button><button onclick="zoomChart('c2','out')">??/button><button onclick="resetChart('c2')">??/button></div>
    </div>
    <div id="c2" style="padding:4px;">{{ chart2_html | safe }}</div>
  </div>

  <div class="section-title">TGA ???쒙㎗?夷?DTS 夷?QRA
    <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ dts_date }} 疫꿸퀣?</span>
    <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank">fiscaldata ??/a>
  </div>

  <div id="dts-qra-tabs">
    <div class="itab-row">
      <button class="itab active" id="dts-qra-tabs-tab-dts" onclick="switchItab('dts-qra-tabs','dts')">DTS ??깆뵬 ??곷열</button>
      <button class="itab" id="dts-qra-tabs-tab-qra" onclick="switchItab('dts-qra-tabs','qra')">QRA ??肄덅쳸?쀫뻬</button>
    </div>

    <!-- DTS ??ㅺ섯 -->
    <div class="itab-panel active" id="dts-qra-tabs-panel-dts">
      {% if dts_error %}
      <div class="error" style="font-size:12px;">DTS ?怨쀬뵠????살첒: {{ dts_error }}</div>
      {% elif not dts_deposits %}
      <div class="loading" style="padding:20px;">DTS ?怨쀬뵠??嚥≪뮆逾?餓?..</div>
      {% else %}
      <div class="dts-grid">
        <div class="dts-card">
          <div class="dts-hd"><span class="dts-dot" style="background:#34d399;"></span>雅뚯눘????껎닊 ????(Table II)
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
          <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>雅뚯눘???곗뮄??????(Table II)
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
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>TGA ?諭????????遺용튋
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

    <!-- QRA ??ㅺ섯 -->
    <div class="itab-panel" id="dts-qra-tabs-panel-qra">
      {% if qra_error %}
      <div class="error" style="font-size:12px;">QRA ?怨쀬뵠????살첒: {{ qra_error }}</div>
      {% elif not qra_data %}
      <div class="loading" style="padding:20px;">QRA ?怨쀬뵠??嚥≪뮆逾?餓?..</div>
      {% else %}
      <!-- 筌롫??껆뵳?燁삳?諭?-->
      <div class="metrics" style="margin-bottom:10px;">
        <div class="mc"><div class="mc-lbl">??쇱벉 QRA 獄쏆뮉紐?/div><div class="mc-val" style="font-size:16px;">{{ qra_data.next_qra }}</div><div class="mc-sub neu">?브쑨由?筌△뫁????륁뒄 獄쏆뮉紐?/div></div>
        <div class="mc"><div class="mc-lbl">筌ㅼ뮄??T-Bill 獄쏆뮉六?(30??</div><div class="mc-val">{{ qra_data.tbill_30d }}</div><div class="mc-sub neg">?醫딅짗????る땾??/div></div>
        <div class="mc"><div class="mc-lbl">筌ㅼ뮄???묒쥚猷울㎖?獄쏆뮉六?(30??</div><div class="mc-val">{{ qra_data.coupon_30d }}</div><div class="mc-sub neg">NL ?類ｌ뺏??/div></div>
        <div class="mc"><div class="mc-lbl">筌ㅼ뮄??TIPS 獄쏆뮉六?(30??</div><div class="mc-val">{{ qra_data.tips_30d }}</div><div class="mc-sub neu">?얠눊??怨뺣짗</div></div>
        <div class="mc"><div class="mc-lbl">???뇧 ?臾믨컳??(BTC)</div><div class="mc-val">{{ qra_data.avg_btc }}</div><div class="mc-sub neu">筌ㅼ뮄??30?????뇧</div></div>
        <div class="mc"><div class="mc-lbl">??獄쏆뮉六?(30??</div><div class="mc-val">{{ qra_data.total_30d }}</div><div class="mc-sub neg">??뽰삢 ??る땾 域뱀뮆???/div></div>
      </div>

      <!-- 獄쏆뮉六??닌딄쉐 獄?-->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>??肄?獄쏆뮉六??닌딄쉐 (筌ㅼ뮄??30??夷??醫딅짗????る땾)
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
          * ??肄?獄쏆뮉六???TGA ?醫롮뿯 ??NL 揶쏅Ŋ?? T-Bill ?袁⑼폒 獄쏆뮉六???MMF(RRP)???怨몃뇵 ??ｋ궢 ??됱벉.
        </div>
      </div>

      <!-- QRA ?癒?즴 疫꿸퀣? -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>QRA ?醫딅짗???癒?즴 疫꿸퀣?</div>
        <div class="dts-row"><span class="dts-name">T-Bill ??쑴夷??誘れ벉</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">RRP???怨몃뇵</span><span class="qra-tag tag-in">NL 餓λ쵎???醫롮뿯</span></div>
        <div class="dts-row"><span class="dts-name">?묒쥚猷울㎖???쑴夷??誘れ벉</span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">????餓Β??쑨????る땾</span><span class="qra-tag tag-out">NL ?類ｌ뺏</span></div>
        <div class="dts-row"><span class="dts-name">筌△뫁??域뱀뮆????됯맒??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA 疫뀀맩弛???뉙?/span><span class="qra-tag tag-out">NL ??롮뵭 ?醫륁깈</span></div>
        <div class="dts-row"><span class="dts-name">筌△뫁??域뱀뮆????됯맒??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?袁⑥춸 ?醫?</span><span class="qra-tag tag-in">NL ??됱젟 ?醫륁깈</span></div>
        <div class="dts-row"><span class="dts-name">?봔筌?쑵釉???臾믨맒 餓?/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ???춭 筌왖??/span><span class="qra-tag tag-in">NL ?紐꾩맄???怨몃뱟</span></div>
        <div class="dts-row"><span class="dts-name">?봔筌?쑵釉????곷꺖 ??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?????/span><span class="qra-tag tag-out">NL 疫뀀맧???袁る퓮</span></div>
      </div>

      <!-- 獄쏆뮉紐???깆젟 -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#fbbf24;"></span>QRA 獄쏆뮉紐???깆젟 (2026)</div>
        <div class="qra-pill-row">
          {% for q in qra_data.schedule %}
          <span class="qra-pill {{ 'hl' if q.current else '' }}">{{ q.label }}</span>
          {% endfor %}
        </div>
        <div style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:8px;">
          TBAC 獄쏆뮉紐??諭????뽰삢 癰궰??덇쉐 雅뚯눘?? 筌△뫁??域뱀뮆?????疫뀀뜄???夷?NL???類ｌ젾.
        </div>
      </div>

      <!-- 筌ㅼ뮄??野껋럥????곷열 -->
      <div class="section-title" style="margin-top:4px;">筌ㅼ뮄??野껋럥????곷열 (30??
        <a class="src-link" href="https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&dateFieldName=auctionDate&startDate={{ qra_data.start_date }}" target="_blank">?癒?궚 ??/a>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th style="text-align:left;">野껋럥???/th>
            <th style="text-align:left;">?ル굝履?/th>
            <th style="text-align:left;">筌띾슡由?/th>
            <th>獄쏆뮉六??B)</th>
            <th>?臾믨컳??/th>
            <th>疫뀀뜄???醫롮뵥??/th>
          </tr></thead>
          <tbody>
            {% for r in qra_data.auctions %}
            <tr>
              <td style="text-align:left;">{{ r.date }}</td>
              <td style="text-align:left;">
                <span class="has-tip" style="font-size:11px;padding:1px 7px;border-radius:4px;background:{{ r.type_bg }};color:{{ r.type_color }};"
                  data-tip-title="{{ r.tip_title }} 夷?{{ r.term }}"
                  data-tip-body="{{ r.tip_body }}"
                  data-tip-liq="{{ r.tip_liq }}"
                  data-tip-neg="{{ 'true' if r.tip_neg else 'false' }}">{{ r.stype }}</span>
              </td>
              <td style="text-align:left;color:rgba(255,255,255,0.4);">{{ r.term }}</td>
              <td>{{ r.amt }}</td>
              <td>
                <span class="{{ 'badge-up' if r.btc_ok else 'badge-dn' }} has-tip"
                  data-tip-title="?臾믨컳??(Bid-to-Cover)"
                  data-tip-body="野껋럩????녾컳 ??뽱뀱??泥???덇컳?? ??륁뒄 揶쏅베猷?筌왖??"
                  data-tip-liq="{{ '2.3x????륁뒄 ?臾볦깈' if r.btc_ok else '2.3x????륁뒄 ?봔鈺?野껋럡?? }}"
                  data-tip-neg="{{ 'false' if r.btc_ok else 'true' }}">{{ r.btc }}</span>
              </td>
              <td>
                <span class="has-tip" style="color:rgba(255,255,255,0.5);"
                  data-tip-title="??덇컳 疫뀀뜄???醫롮뵥??
                  data-tip-body="{{ 'T-Bill: ?醫롮뵥??Discount Rate) 疫꿸퀣?. ?誘れ뱽??롮쨯 ??ｋ┛ ?癒?닊 ??쑴???' if r.is_bill else '?묒쥚猷울㎖? 筌ㅼ뮄????덇컳 ??륁뵡??High Yield). ?誘れ뱽??롮쨯 ??????곸쁽 ?봔??노꼤 夷?NL ?觀由??類ｌ뺏.' }}"
                  data-tip-liq="{{ '??ｋ┛疫뀀뜄??獄쎻뫚堉??筌왖?? if r.is_bill else '?觀由경묾?댿봺????雅뚯눘??筌렺?怨좊탣 ?類ｌ뺏' }}"
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

  <div class="section-title">??????源??筌?꼶???    <a class="src-link" href="https://www.irs.gov/businesses/small-businesses-self-employed/tax-calendar" target="_blank">IRS Calendar ??/a>
  </div>
  <div class="chart-card" style="padding:14px 16px;margin-bottom:12px;">
    <div class="cal-legend">
      <span><span class="cal-legend-dot" style="background:#34d399;"></span>?醫딅짗???醫롮뿯 (??랁닋夷?類?筌왖??</span>
      <span><span class="cal-legend-dot" style="background:#f87171;"></span>?醫딅짗???醫롰뀱 (?硫명닊???夷뚧뤃?肄덅쳸?쀫뻬)</span>
      <span><span class="cal-legend-dot" style="background:rgba(255,255,255,0.2);"></span>餓λ쵎??獄쏆뮉紐?/span>
    </div>
    <div class="cal-grid">
      <div class="cal-m"><div class="cal-mn">1??/div>
        <span class="cal-ev ev-out">4Q ?곕뗄?????? (1/15)</span>
        <span class="cal-ev ev-neu">IRS ?醫됲??뽰サ 揶쏆뮇??/span>
        <span class="cal-ev ev-in">???띈퉪?곸삢夷뚳쭖遺얜탵?냈??노꼤</span>
        <span class="cal-ev ev-neu">QRA 獄쏆뮉紐?~1/29)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">2??/div>
        <span class="cal-ev ev-in">??랁닋 ??녠쾿 (W-2)?臾낅꼤</span>
        <span class="cal-ev ev-in">EITC夷똂TC ??랁닋 揶쏆뮇??/span>
        <span class="cal-ev ev-in">???띈퉪?곸삢夷뚳쭖遺얜탵?냈??노꼤</span>
        <span class="cal-ev ev-neu">H.4.1 筌띲끉竊???륁뒄??/span>
      </div>
      <div class="cal-m"><div class="cal-mn">3??/div>
        <span class="cal-ev ev-in">??랁닋 筌왖???꼤</span>
        <span class="cal-ev ev-neu">S-Corp夷??곕뱜??됰뼏 ?醫됲?3/15)</span>
        <span class="cal-ev ev-neu">T-Note ?브쑨由계쳸?쀫뻬</span>
        <span class="cal-ev ev-out">??肄?筌띾슡由곗쮯嚥▲끉?ㅸ린袁잙꼦</span>
      </div>
      <div class="cal-m hl-red"><div class="cal-mn red">4????/div>
        <span class="cal-ev ev-out">Tax Day (4/15)?蹂쏅꼦</span>
        <span class="cal-ev ev-out">1Q ?곕뗄???(4/15)??/span>
        <span class="cal-ev ev-out">TGA 疫뀀맩弛???NL 揶쏅Ŋ??/span>
        <span class="cal-ev ev-neu">?怨쀬삢?醫롪퍕(Form 4868)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">5??/div>
        <span class="cal-ev ev-in">?遺용연 ??랁닋 筌왖???꼤</span>
        <span class="cal-ev ev-neu">Form 990 ??쑴?븀뵳??醫됲?/span>
        <span class="cal-ev ev-in">?類? 筌왖???類ㅺ맒?遺대꼤</span>
        <span class="cal-ev ev-neu">QRA 獄쏆뮉紐?~4?遺얠춾)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">6??/div>
        <span class="cal-ev ev-out">2Q ?곕뗄???(6/15)??/span>
        <span class="cal-ev ev-in">??媛묒쮯?紐낅늄??筌왖?곗뭼??/span>
        <span class="cal-ev ev-neu">T-Bill ?類?┛ 嚥▲끉?ㅸ린?/span>
        <span class="cal-ev ev-neu">FOMC ???벥(???맒)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">7??/div>
        <span class="cal-ev ev-in">???띈퉪?곸삢 筌왖疫뀀맍??/span>
        <span class="cal-ev ev-in">筌롫뗀逾믦냈??붾８李?遺???諭??/span>
        <span class="cal-ev ev-in">??已??紐낅늄??筌왖?곗뭼??/span>
        <span class="cal-ev ev-neu">QRA 獄쏆뮉紐?~7/28)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">8??/div>
        <span class="cal-ev ev-out">T-Bill ??域뱀뮆??獄쏆뮉六??/span>
        <span class="cal-ev ev-neu">QRA夷똖BAC 獄쏆뮉紐?/span>
        <span class="cal-ev ev-in">?類? ???억쭪??곗뭼??/span>
        <span class="cal-ev ev-neu">????? ?怨쀪퐬(?怨?)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">9??/div>
        <span class="cal-ev ev-out">3Q ?곕뗄???(9/15)??/span>
        <span class="cal-ev ev-in">????怨뺣즲 筌띾뜃而?筌왖?곗뭼???/span>
        <span class="cal-ev ev-out">??肄??브쑨由?獄쏆뮉六??/span>
        <span class="cal-ev ev-neu">????怨뺣즲 ?ル굝利?9/30)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">10??/div>
        <span class="cal-ev ev-neu">??????怨뺣즲 揶쏆뮇??FY)</span>
        <span class="cal-ev ev-neu">?怨쀬삢 筌띾뜃而?10/15)</span>
        <span class="cal-ev ev-in">???띈퉪?곸삢 COLA ?紐꾧맒??/span>
        <span class="cal-ev ev-neu">TIC ?怨쀬뵠??獄쏆뮉紐?~18??</span>
      </div>
      <div class="cal-m"><div class="cal-mn">11??/div>
        <span class="cal-ev ev-in">?怨뺤춾 ?類? 筌왖?곗뭼??/span>
        <span class="cal-ev ev-in">???띈퉪?곸삢夷뚩퉪??筌왖?곗뭼??/span>
        <span class="cal-ev ev-neu">QRA 獄쏆뮉紐?~10?遺얠춾)</span>
        <span class="cal-ev ev-neu">T-Bond ?브쑨由계쳸?쀫뻬</span>
      </div>
      <div class="cal-m hl-green"><div class="cal-mn green">12????/div>
        <span class="cal-ev ev-in">筌왖????녠쾿?臾낅꼤 (???롳쭕?뉗빵)</span>
        <span class="cal-ev ev-out">?怨뺤춾 ?硫명닊 ?????/span>
        <span class="cal-ev ev-in">???띈퉪?곸삢 ?醫?疫뀀맍??/span>
        <span class="cal-ev ev-neu">?怨? 筌ㅼ뮇伊?FOMC</span>
      </div>
    </div>
  </div>

  <details class="collapsible">
    <summary>??뽰삢 ?醫딅짗??疫꿸퀣? <a class="src-link" href="https://www.federalreserve.gov/releases/h41/" target="_blank" onclick="event.stopPropagation()">H.4.1 ??/a></summary>
    <div class="collapsible-body">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:12px;line-height:1.8;">
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">?諭??醫딅짗???醫롮뿯 ?醫륁깈 (NL ?怨몃뱟 鈺곌퀗援?</div>
          <div class="dts-row"><span class="dts-name">WALCL 筌앹빓?</span><span style="color:#34d399;font-size:11px;">Fed ?癒?텦 筌띲끉??????뽰㉦ ?癒?닊??/span></div>
          <div class="dts-row"><span class="dts-name">TGA 揶쏅Ŋ??/span><span style="color:#34d399;font-size:11px;">??龜?봔 筌왖????????餓Β??쑨???/span></div>
          <div class="dts-row"><span class="dts-name">RRP 揶쏅Ŋ??/span><span style="color:#34d399;font-size:11px;">MMF ?癒?닊 ??뽰삢 ?醫롮뿯??/span></div>
          <div class="dts-row"><span class="dts-name">?봔筌?쑵釉???臾믨맒</span><span style="color:#34d399;font-size:11px;">TGA ???춭 ??NL 疫뀀맩湲??/span></div>
          <div class="dts-row"><span class="dts-name">QE ??而?/span><span style="color:#34d399;font-size:11px;">WALCL ?類? ??筌욊낯???醫딅짗?湲곕꼤</span></div>
          <div class="dts-row"><span class="dts-name">??랁닋 ??뽰サ (2~3??</span><span style="color:#34d399;font-size:11px;">TGA 揶쏅Ŋ?쇱쮯???돩??/span></div>
          <div class="dts-row"><span class="dts-name">SRF夷?類ㅼ퐠 ????/span><span style="color:#34d399;font-size:11px;">Fed 疫뀀떯???醫딅짗???⑤벀???/span></div>
          <div class="dts-row"><span class="dts-name">?紐낆넎癰귣똻? ??????롮첒</span><span style="color:#34d399;font-size:11px;">??곸뇚 餓λ쵐釉??????쇱넁??깆뵥??/span></div>
        </div>
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">?諭??醫딅짗???醫롰뀱 ?醫륁깈 (NL ??롮뵭 鈺곌퀗援?</div>
          <div class="dts-row"><span class="dts-name">WALCL 揶쏅Ŋ??(QT)</span><span style="color:#f87171;font-size:11px;">Fed ?癒?텦 ?곕벡????餓Β??쑨??揶쏅Ŋ???/span></div>
          <div class="dts-row"><span class="dts-name">TGA 疫뀀맩弛?/span><span style="color:#f87171;font-size:11px;">?硫명닊???夷뚧뤃?肄덅쳸?쀫뻬 ????뽰㉦ ??る땾??/span></div>
          <div class="dts-row"><span class="dts-name">RRP 筌앹빓?</span><span style="color:#f87171;font-size:11px;">MMF揶쎛 Fed???癒?닊 ??됲뒄??/span></div>
          <div class="dts-row"><span class="dts-name">Tax Day (4??</span><span style="color:#f87171;font-size:11px;">TGA 疫뀀맩弛???NL ??ｋ┛ ?類ｌ뺏??/span></div>
          <div class="dts-row"><span class="dts-name">?곕뗄??????(?브쑨由?</span><span style="color:#f87171;font-size:11px;">1/15 夷?4/15 夷?6/15 夷?9/15??/span></div>
          <div class="dts-row"><span class="dts-name">T-Bill ??域뱀뮆??獄쏆뮉六?/span><span style="color:#f87171;font-size:11px;">??뽰㉦ ?癒?닊 ??肄덃에???る땾??/span></div>
          <div class="dts-row"><span class="dts-name">?봔筌?쑵釉????곷꺖 ??/span><span style="color:#f87171;font-size:11px;">TGA ???????NL 疫뀀맧???/span></div>
          <div class="dts-row"><span class="dts-name">疫꿸퀣?疫뀀뜄???紐꾧맒</span><span style="color:#f87171;font-size:11px;">RRP 疫뀀뜄??筌띲끇??????癒?닊?醫롰뀱??/span></div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:rgba(255,255,255,0.25);">
        ?裕?<b style="color:rgba(255,255,255,0.4);">???뼎 ?⑤벊??</b> NL = WALCL ??TGA ??RRP &nbsp;夷?nbsp;
        NL???怨몃뱟??롢늺 ??뽰㉦ ?醫딅짗??筌앹빓? ???袁る퓮?癒?텦 ?醫륁깈 野껋?堉?&nbsp;夷?nbsp;
        <a href="https://fred.stlouisfed.org/series/WALCL" target="_blank" style="color:#60a5fa;text-decoration:none;">WALCL??/a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank" style="color:#60a5fa;text-decoration:none;">TGA??/a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank" style="color:#60a5fa;text-decoration:none;">RRP??/a>
      </div>
    </div>
  </details>

  <details class="collapsible">
    <summary>?④쑴沅?獄쎻뫖苡욘에?/summary>
    <div class="collapsible-body">
      <div class="method-box" style="margin-bottom:0;">
        <h3>1. Net Liquidity</h3>
        <div class="formula">NL = WALCL ??TGA ??RRP</div>
        <div class="desc"><b>WALCL</b>: Fed ?μ빘?????筌띾‘???롮쨯 ??뽰㉦????됱뵠 筌띾‘???????怨밴묶</div>
        <div class="desc"><b>TGA 筌△몿而?/b>: ??龜?봔揶쎛 Fed????됲뒄???袁㏉닊 ????뽰삢?????귐? ??? ??/div>
        <div class="desc"><b>RRP 筌△몿而?/b>: MMF ?源놁뵠 Fed??筌띯넄由???????遺용만 ????뽰삢 獄쏅쉼肉???덈뮉 ??/div>
        <div class="desc" style="margin-top:6px;">??Michael Howell(CrossBorder Capital), Lyn Alden ?源놁뵠 ??餓λ쵑?? Fed ?醫딅짗?源놁뵠 ??쇱젫嚥???뽰삢????곗춳??????쇱뿳?遺? 筌β돦??</div>
        <h3 style="margin-top:14px;">2. NL ??? ?⑤벊?쇿첎?燁?/h3>
        <div class="formula">SPX_FV = slope ??NL + intercept</div>
        <div class="desc">2000?袁????袁⑹삺繹먮슣? ??⑥퍢 ?怨쀬뵠?怨뺤쨮 ?醫륁굨???. NL????SPX ?⑤벊?쇿첎?燁살꼦???온??筌뤴뫀?쏙쭕?</div>
        {% if model_info %}<div class="model-info">slope={{ model_info.slope }} | intercept={{ model_info.intercept }} | R吏?{{ model_info.r2 }} | n={{ model_info.n }}</div>{% endif %}
        <h3 style="margin-top:14px;">3. ?용????/h3>
        <div class="formula">?용????= (SPX?袁⑹삺揶쎛 ??FV) / FV ??100 (%)</div>
        <div class="desc">?臾믩땾(+): ?⑥쥚猷듿첎? &nbsp;|&nbsp; ???땾(??: ?????</div>
        <div class="warn">??NL?遊뻇X ?怨??온??R吏??.6~0.8)????뺣궚 疫꿸퀗而????뤵??렽? ?硫몃궢?온?④쑨? ?袁⑤빒 ?怨??온?④쑴???덈뼄. ?????FV癰귣???<b>獄쎻뫚堉?援용０?쇘뵳??곕뗄苑?/b> ?袁⑼폒嚥???뽰뒠 亦낅슣??</div>
      </div>
    </div>
  </details>

  <div class="section-title">?遺용튋</div>
  <div class="summary-box">
    <div class="row"><span class="lbl">疫꿸퀣???/span><span class="val">{{ summary.base_date }}</span></div>
    <div class="row"><span class="lbl">WALCL ({{ summary.walcl_date }})</span><span class="val">{{ summary.walcl_raw }}</span></div>
    <div class="row"><span class="lbl">TGA ({{ summary.tga_date }})</span><span class="val">{{ summary.tga_raw }}</span></div>
    <div class="row"><span class="lbl">RRP ({{ summary.rrp_date }})</span><span class="val">{{ summary.rrp_raw }}</span></div>
    <div class="row"><span class="lbl">Net Liquidity</span><span class="val {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_raw }} &nbsp;({{ summary.nl_chg }})</span></div>
    <hr class="divider">
    <div class="row"><span class="lbl">NL ??? ?⑤벊?쇿첎?燁?/span><span class="val">{{ summary.fv_nl }}</span></div>
    <div class="row"><span class="lbl">SPX ?袁⑹삺揶쎛</span><span class="val {{ 'pos' if summary.fv_nl_cheap else 'neg' }}">{{ summary.spx_raw }} &nbsp;({{ summary.fv_nl_gap }})</span></div>
  </div>

  <div class="section-title">筌ㅼ뮄??10 ?怨몃씜???怨쀬뵠??/div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>?醫롮?</th><th style="text-align:right;">WALCL(B)</th><th style="text-align:right;">TGA(B)</th><th style="text-align:right;">RRP(B)</th><th style="text-align:right;">Net Liq(B)</th><th style="text-align:right;">DoD</th><th style="text-align:right;">SP500</th><th style="text-align:right;">NL FV</th><th style="text-align:right;">?용????/th></tr></thead>
      <tbody>
        {% for row in table_rows %}
        <tr>
          <td>{{ row.date }}</td><td>{{ row.walcl }}</td><td>{{ row.tga }}</td><td>{{ row.rrp }}</td>
          <td><strong>{{ row.nl }}</strong></td>
          <td>{% if row.dod_pos is none %}<span style="color:rgba(255,255,255,0.3);font-size:11px;">{{ row.dod }}</span>{% elif row.dod_pos %}<span class="badge-up">{{ row.dod }}</span>{% else %}<span class="badge-dn">{{ row.dod }}</span>{% endif %}</td>
          <td>{{ row.spx }}</td><td>{{ row.fv_nl }}</td>
          <td>{% if row.gap is not none %}<span class="{{ 'badge-dn' if row.gap_pos else 'badge-up' }}">{{ row.gap }}</span>{% else %}??% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

{% endif %}
</div>

<div id="tab-tic" class="tab-content">
{% if tic_error %}
  <div class="error">TIC ?怨쀬뵠????살첒: {{ tic_error }}</div>
{% elif not tic_chart_html %}
  <div class="loading">TIC ?怨쀬뵠??嚥≪뮆逾?餓?..</div>
{% else %}

  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">雅뚯눘?귝뤃?沃섎㈇?낉㎖?癰귣똻?????Monthly (2000?諭럕esent)
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

  <div class="section-title">筌ㅼ뮇??癰귣똻?????뽰맄 <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ tic_updated_at }} 疫꿸퀣? 夷???6雅??袁る뻬 獄쏆뮉紐?/span></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>#</th><th>???</th><th style="text-align:right;">癰귣똻???(B)</th><th style="text-align:right;">?袁⑹뜞癲?/th><th style="text-align:right;">??쑴夷?/th></tr></thead>
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
    <b style="color:#cc0000;">TIC ?怨쀬뵠?怨??</b><br>
    Treasury International Capital ??沃???龜?봔揶쎛 筌띲끉??獄쏆뮉紐??롫뮉 ?硫몃럢?紐꾩벥 沃섎㈇?낉㎖?癰귣똻? ?袁れ넺. 餓λ쵌?낆쮯??곕궚??癰귣똻???癰궰?遺얜뮉 ??????ｍ뀙 獄?沃섎㈇?낉㎖?疫뀀뜄????怨밸샨??沃섎챷??????뼎 筌왖??<br><br>
    <b style="color:#555;">獄쏆뮉紐???깆젟 (筌띲끉??18??④펾):</b><br>
    &nbsp;夷?1???怨쀬뵠????3??18??獄쏆뮉紐?br>
    &nbsp;夷?2???怨쀬뵠????4??18??獄쏆뮉紐?br>
    &nbsp;夷?3???怨쀬뵠????5??18??獄쏆뮉紐?br>
    &nbsp;夷?<i>??꾨릭 ??덉뵬 ????湲???6雅??袁る뻬</i><br><br>
    <b style="color:#555;">雅뚯눘??</b> 癰귣똻???? custodian 疫꿸퀣? ??餓λ쵌??????癒? 甕겸몿由??????깅퓠 ??딄맒 ??甕겸몿由?癒?쨮 筌욌쵌?? ?룐뫗?띺겫??쒕똾寃뺤쮯?냈???ㅼ쮯甕겸몿由????疫뀀뜆????덊닏???誘? ??륂뒄????쇱젫 ????뤃????袁⑤빒 ?????癒?닊??揶쎛?關苑???誘れ벉.
  </div>

{% endif %}
</div>

  <div class="footer">
    Net Liquidity: <a href="https://fred.stlouisfed.org" target="_blank" style="color:#60a5fa;text-decoration:none;">FRED</a> (WALCL夷똚DTGAL夷똓RPONTSYD夷똕P500) &nbsp;|&nbsp;
    TGA ???쒙㎗? <a href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank" style="color:#60a5fa;text-decoration:none;">fiscaldata.treasury.gov</a> &nbsp;|&nbsp;
    ???癰?沃섎㈇?낉㎖? <a href="https://home.treasury.gov/data/treasury-international-capital-tic-system" target="_blank" style="color:#60a5fa;text-decoration:none;">U.S. Treasury TIC</a> &nbsp;|&nbsp; 2000?諭럕esent
  </div>
</div>
</body>
</html>
"""

