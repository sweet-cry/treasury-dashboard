"""
Net Liquidity + ?????æ²ƒì„???‰ã–??°ê·£??? Dashboard
=============================================
Vercel + Neon(PostgreSQL) ?•ê³Œ???
???»í¾?°ê¶°???
  FRED_API_KEY  : FRED API Key
  DATABASE_URL  : Neon PostgreSQL ??¨ëš­????œì±·???  START_DATE    : ??ë½°ì‚‚??(?«ê¿¸???2000-01-01)
  CRON_SECRET   : Cron ??ºì–œêµ??????°ê·£???????ªì¾¿????
???…ì‘“??ê¾¨ë±œ ???é¤?(vercel.json cron):
  - NL/DTS/QRA : ç­Œë²???00:30 UTC
  - TIC        : ç­Œë²???18??02:00 UTC
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
# Neon DB ??«ë¤¿ë¼?# ????????????????????????????????????????????????????????????????????????????????????????????

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """ç­?ÅŠ?????ëµ???Î»?ƒç”±??(ç­Œã…¼ë®??1??"""
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
# FRED ??¨ì€¬ëµ ??fetch
# ????????????????????????????????????????????????????????????????????????????????????????????

def fetch_series(series_id, start, frequency="d"):
    if not API_KEY:
        raise ValueError("FRED_API_KEY ???»í¾?°ê¶°???? ???±ì Ÿ??? ???¿ë¦­???ë¹??")
    params = dict(series_id=series_id, api_key=API_KEY, file_type="json",
                  observation_start=start, frequency=frequency)
    r = req.get(FRED_BASE, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error_message" in data:
        raise ValueError(f"{series_id}: {data['error_message']}")
    obs = [(o["date"], float(o["value"])) for o in data["observations"] if o["value"] != "."]
    if not obs:
        raise ValueError(f"{series_id}: ??¨ì€¬ëµ ????ê³¸ë²‰")
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
    raise ValueError(f"{series_id}: ?????¶ì›??é¤¨é‡‰?frequency ??ê³¸ë²‰")


# ????????????????????????????????????????????????????????????????????????????????????????????
# NL ??£ì‘´æ²?# ????????????????????????????????????????????????????????????????????????????????????????????

def fmt_val(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "??"
    if v != v:
        return "??"
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

    # yfinance fallback: FREDë³´ë‹¤ ìµœì‹  ?°ì´??ë³´ì™„
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

    # yfinance fallback: FREDè¹‚ë???ï§¤ì’–???ê³—ì” ??è¹‚ëŒ??    try:
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
        fv_nl_gap = f"{'+' if gap>0 else ''}{gap:.1f}% {'??¥ì¥š?·ë“¿ì²?' if gap>0 else '?????'}"
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
        "fv_nl_gap": fv_nl_gap or "??¨ì€¬ëµ ???ë´”Â€??, "fv_nl_cheap": fv_nl_cheap,
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
        {"month": 2, "label": "???ë‹‹ ??? ì¾¿", "color": "rgba(52,211,153,0.5)"},
        {"month": 3, "label": "???ë‹‹ ??? ì¾¿", "color": "rgba(52,211,153,0.5)"},
        {"month": 4, "label": "Tax Day",   "color": "rgba(248,113,113,0.6)"},
        {"month": 6, "label": "2Q ?ê³•ë—„???, "color": "rgba(251,191,36,0.5)"},
        {"month": 9, "label": "3Q ?ê³•ë—„???, "color": "rgba(251,191,36,0.5)"},
        {"month": 1, "label": "4Q ?ê³•ë—„???, "color": "rgba(251,191,36,0.5)"},
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
        raise ValueError("TIC ??¨ì€¬ëµ ?????ë¼????ˆì†­")
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
        return "??"
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
        raise ValueError("DTS Table II ??¨ì€¬ëµ ????ê³¸ë²‰")

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
        {"name": "????ê»ë‹Š (Total Deposits)",    "amt": fmt_mil(total_dep), "pos": True},
        {"name": "???ê³—ë®„??(Total Withdrawals)", "amt": fmt_mil(total_wit), "pos": False},
        {"name": f"?è«?€???????({'??«ë¡®ë¿? if net>=0 else '??«ë¡°??})", "amt": fmt_mil(abs(net)), "pos": net >= 0},
    ]
    return dep_list, wit_list, balance_list, latest_date


# ????????????????????????????????????????????????????????????????????????????????????????????
# QRA
# ????????????????????????????????????????????????????????????????????????????????????????????

TIP_INFO = {
    "Bill": {"title": "Treasury Bill", "body": "ç­Œë¾?¡ç”±?1????ê¾¨ë¦­ ??ï½‹â”› ???? MMF?¶ì›? ?…ëš¯???ç­Œë²??????T-Bill ?„ì†ë®‰ï§‘????RRP????¨ëªƒ????NL ?ê²¸ë«•?????«ë¦½.", "liq": "NL ??¨ë°¸?????«ë¦½ (RRP ??¨ëªƒ??", "neg": False},
    "Note": {"title": "Treasury Note (2~10Y)", "body": "é¤“Î»ìµŒ?????? ??????·ë°¸?¼ç–«ê¿¸í€??ç­Œë²?????é¤“Î’Â€?????ç­ŒìšŠ??????‹ë•¾ ??NL ??ë¡?µ­ ?ï§ï½Œ??", "liq": "????é¤“Î’Â€????????‹ë•¾ ??NL??, "neg": True},
    "Bond": {"title": "Treasury Bond (20~30Y)", "body": "?è§€?????? ?????±ëµ ???èª˜ã‚‹???è§€???«ë€€???æ²ƒì„?Šè€?", "liq": "?è§€?±ê²½ë¬??¿ë´º ?ê»‹?¥ä»¥?ƒì—??¶ì„???NL ?ï§ï½Œëº?, "neg": True},
    "TIPS": {"title": "TIPS (?? ëˆŠ???¨ëº£ì§?", "body": "??????CPI????¨ëº£ì§? ???±ì¶³?«ë€€???ç­Œì™–???", "liq": "???±ì¶³?«ë€€???ç­Œì™–?????ç­ŒìšŠ?????ï½‹ê¶¢ ???«ë¦½??, "neg": False},
    "FRN":  {"title": "FRN (?°ê¶°????ƒë‹Š?ê·ë”†??", "body": "13??T-Bill ?«ë€€??????¨ëº£ì§? ??ï½‹â”›?? ëˆ˜???¶ì›?ç¹¹ë¨®?????«ë”…ì§???è«?€??", "liq": "??ï½‹â”›????«ë¡ª????NL ??¨ë°¸?????«ë¦½??, "neg": False},
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
        raise ValueError("QRA ?ê»‹?????¨ì€¬ëµ ????ê³¸ë²‰")

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
        {"label": "Q1: 2026-01-27 ?è¢â‘¥??, "current": False},
        {"label": "Q2: 2026-04-28 ???±ì Ÿ", "current": True},
        {"label": "Q3: 2026-07-27 ???±ì Ÿ", "current": False},
        {"label": "Q4: 2026-10-27 ???±ì Ÿ", "current": False},
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
# Cron ?¶ì„?????Î»??(Neon??????
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
        print("NL ?¶ì„????è¢â‘¥??)
    except Exception as e:
        db_set("nl_error", str(e))
        print(f"NL ???´ì²’: {e}")


def run_refresh_tic():
    try:
        pivot = fetch_tic_data()
        db_set("tic_chart",      build_tic_chart(pivot))
        db_set("tic_table",      build_tic_table(pivot))
        db_set("tic_updated_at", pivot.index[-1].strftime("%Y-%m"))
        db_set("tic_error",      None)
        print("TIC ?¶ì„????è¢â‘¥??)
    except Exception as e:
        db_set("tic_error", str(e))
        print(f"TIC ???´ì²’: {e}")


def run_refresh_dts():
    try:
        dep, wit, bal, date = fetch_dts_data()
        db_set("dts_deposits",    dep)
        db_set("dts_withdrawals", wit)
        db_set("dts_balance",     bal)
        db_set("dts_date",        date)
        db_set("dts_error",       None)
        print(f"DTS ?¶ì„????è¢â‘¥?? {date}")
    except Exception as e:
        db_set("dts_error", str(e))
        print(f"DTS ???´ì²’: {e}")


def run_refresh_qra():
    try:
        db_set("qra_data",  fetch_qra_data())
        db_set("qra_error", None)
        print("QRA ?¶ì„????è¢â‘¥??)
    except Exception as e:
        db_set("qra_error", str(e))
        print(f"QRA ???´ì²’: {e}")


# ????????????????????????????????????????????????????????????????????????????????????????????
# Flask ??ê¹†ë’­??# ????????????????????????????????????????????????????????????????????????????????????????????

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
# DB ?Î»?ƒç”±??+ ç­???¨ì€¬ëµ ???¥â‰ªë®†é€?# ????????????????????????????????????????????????????????????????????????????????????????????

if DATABASE_URL:
    try:
        init_db()
        # DB????¨ì€¬ëµ ??? ??ê³¸ë±½ ???ì¶??Î»?ƒç”±??¥â‰ªë®†é€?        if db_get("nl_summary") is None:
            print("?Î»?ƒç”±???¨ì€¬ëµ ????ê³¸ë²‰ ???„ì„????ê¹†ë’²???¥â‰ªë®†é€???ë½°ì‚‚")
            for fn in [run_refresh_nl, run_refresh_tic, run_refresh_dts, run_refresh_qra]:
                threading.Thread(target=fn, daemon=True).start()
    except Exception as e:
        print(f"DB ?Î»?ƒç”±?????´ì²’: {e}")
else:
    print("WARNING: DATABASE_URL ???»í¾?°ê¶°???? ???±ì Ÿ??? ???¿ë¦­???ë¹??")

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
    /* DTS ?è«?€??*/
    .dts-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;}
    .dts-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:14px 16px;}
    .dts-hd{font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;display:flex;align-items:center;gap:6px;}
    .dts-dot{width:6px;height:6px;border-radius:50%;display:inline-block;}
    .dts-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);}
    .dts-row:last-child{border-bottom:none;}
    .dts-name{font-size:12px;color:rgba(255,255,255,0.4);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:10px;}
    .dts-amt{font-size:12px;font-weight:500;white-space:nowrap;}
    .c-in{color:#34d399;}.c-out{color:#f87171;}
    /* ç­?ê¼???*/
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
    /* QRA ?ê»‹?????ê¾¨ìƒ - JS ??ë½?„  fixed ??ë°¸ì”œ */
    #auction-tooltip{display:none;position:fixed;z-index:9999;
      background:#1a1a22;border:1px solid rgba(255,255,255,0.15);border-radius:8px;
      padding:10px 13px;width:230px;pointer-events:none;
      font-size:11px;line-height:1.55;color:rgba(255,255,255,0.45);}
    #auction-tooltip b{color:rgba(255,255,255,0.8);font-weight:500;display:block;margin-bottom:4px;}
    #auction-tooltip .tip-liq{margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.08);font-size:11px;}
    #auction-tooltip .tip-neg{color:#f87171;font-weight:500;}
    #auction-tooltip .tip-neu{color:rgba(255,255,255,0.45);font-weight:500;}
    .has-tip{cursor:default;}
    /* ?ï§ê»‹ëµ????(DTS/QRA) */
    .itab-row{display:flex;gap:4px;margin-bottom:10px;}
    .itab{font-size:11px;padding:4px 14px;border:1px solid rgba(255,255,255,0.1);border-radius:20px;background:transparent;cursor:pointer;color:rgba(255,255,255,0.3);transition:all .15s;}
    .itab.active{background:rgba(96,165,250,0.12);border-color:rgba(96,165,250,0.35);color:#60a5fa;}
    .itab-panel{display:none;}.itab-panel.active{display:block;}
    /* QRA ??ç­Œâ–³ë«??*/
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
    /* ??¾ë¡«????ê¹Šë’„??*/
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
      document.getElementById('cd').textContent='?¶ì„???é¤?..';
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
    // ?ê»‹?????ê¾¨ìƒ (ç­Œë¾?????è¢â‘º???«ê¿¸?¡è€?fixed)
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
  <div class="tab" id="tab-btn-tic" onclick="switchTab('tic')">?????æ²ƒì„???‰ã–??°ê·£???</div>
</div>

<div id="tab-nl" class="tab-content active">
{% if error %}
  <div class="error">Error: {{ error }}</div>
{% elif not summary %}
  <div class="loading">FRED ??¨ì€¬ëµ ???¥â‰ªë®†é€?é¤?.. ??«ë¡«ë»??????ì§????‰ì¨®??¥ì¥™???ëªƒë¹??</div>
{% else %}

  <div class="metrics">
    <div class="mc"><div class="mc-lbl">Net Liquidity</div><div class="mc-val">{{ summary.nl }}</div><div class="mc-sub {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_chg }}</div></div>
    <div class="mc"><div class="mc-lbl">NL Regression FV</div><div class="mc-val">{{ summary.fv_nl }}</div><div class="mc-sub {{ 'pos' if summary.fv_nl_cheap else ('neg' if summary.fv_nl_cheap is not none else 'neu') }}">{{ summary.fv_nl_gap }}</div></div>
    <div class="mc"><div class="mc-lbl">WALCL <span style="font-weight:400;color:#bbb;">?…ëš¯?Šè€?/span> <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.walcl }}</div><div class="mc-sub neu">{{ summary.walcl_date }} å¤?H.4.1 ç­Œë²?‰ç«Š???ë¥ë’„??/div></div>
    <div class="mc"><div class="mc-lbl">TGA <span style="font-weight:400;color:#bbb;">?…ëš¯?Šè€?/span> <a class="src-link" href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.tga }}</div><div class="mc-sub neu">{{ summary.tga_date }} å¤????±ë²‰ ?„ì†ë®‰ï§?~{{ next_h41 }}</div></div>
    <div class="mc"><div class="mc-lbl">RRP <span style="font-weight:400;color:#bbb;">???¥í¢</span> <a class="src-link" href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank">FRED??/a></div><div class="mc-val">{{ summary.rrp }}</div><div class="mc-sub neu">{{ summary.rrp_date }}</div></div>
    <div class="mc"><div class="mc-lbl">S&P 500</div><div class="mc-val">{{ summary.spx_raw }}</div><div class="mc-sub neu">{{ summary.base_date }}</div></div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">WALCL ??Œë”„?? Net Liquidity å¤?TGA å¤?RRP ??Daily (2000?è«?Ÿ•esent)
        <a class="src-link" href="https://fred.stlouisfed.org/series/WALCL" target="_blank">FRED ??/a>
      </div>
      <div class="legend">
        <span><span style="width:12px;height:8px;background:rgba(96,165,250,0.6);border-radius:2px;display:inline-block;"></span>Net Liquidity</span>
        <span><span style="width:12px;height:8px;background:rgba(52,211,153,0.55);border-radius:2px;display:inline-block;"></span>TGA</span>
        <span><span style="width:12px;height:8px;background:rgba(251,191,36,0.55);border-radius:2px;display:inline-block;"></span>RRP</span>
        <span style="font-size:10px;color:rgba(255,255,255,0.2);">???ê²? ?ê»‹?¡ç”±ê³ã‰?Îº??/span>
      </div></div>
      <div class="zoom-btns"><button onclick="zoomChart('c1','in')">+</button><button onclick="zoomChart('c1','out')">??/button><button onclick="resetChart('c1')">??/button></div>
    </div>
    <div id="c1" style="padding:4px;">{{ chart1_html | safe }}</div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div><div class="chart-title">S&P 500 vs NL Regression FV ??Daily (2000?è«?Ÿ•esent)
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

  <div class="section-title">TGA ????™ã—?å¤?DTS å¤?QRA
    <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ dts_date }} ?«ê¿¸??</span>
    <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank">fiscaldata ??/a>
  </div>

  <div id="dts-qra-tabs">
    <div class="itab-row">
      <button class="itab active" id="dts-qra-tabs-tab-dts" onclick="switchItab('dts-qra-tabs','dts')">DTS ??ê¹†ëµ¬ ??ê³·ì—´</button>
      <button class="itab" id="dts-qra-tabs-tab-qra" onclick="switchItab('dts-qra-tabs','qra')">QRA ???„ë…ì³??«ë»¬</button>
    </div>

    <!-- DTS ???ºì„¯ -->
    <div class="itab-panel active" id="dts-qra-tabs-panel-dts">
      {% if dts_error %}
      <div class="error" style="font-size:12px;">DTS ??¨ì€¬ëµ ?????´ì²’: {{ dts_error }}</div>
      {% elif not dts_deposits %}
      <div class="loading" style="padding:20px;">DTS ??¨ì€¬ëµ ???¥â‰ªë®†é€?é¤?..</div>
      {% else %}
      <div class="dts-grid">
        <div class="dts-card">
          <div class="dts-hd"><span class="dts-dot" style="background:#34d399;"></span>?…ëš¯?????ê»ë‹Š ????(Table II)
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
          <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>?…ëš¯????ê³—ë®„??????(Table II)
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
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>TGA ?è«?€?????????ºìš©??          <a class="src-link" href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/operating-cash-balance" target="_blank">??/a>
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

    <!-- QRA ???ºì„¯ -->
    <div class="itab-panel" id="dts-qra-tabs-panel-qra">
      {% if qra_error %}
      <div class="error" style="font-size:12px;">QRA ??¨ì€¬ëµ ?????´ì²’: {{ qra_error }}</div>
      {% elif not qra_data %}
      <div class="loading" style="padding:20px;">QRA ??¨ì€¬ëµ ???¥â‰ªë®†é€?é¤?..</div>
      {% else %}
      <!-- ç­Œë¡«??ê»†ëµ³??ì‚³?è«?-->
      <div class="metrics" style="margin-bottom:10px;">
        <div class="mc"><div class="mc-lbl">???±ë²‰ QRA ?„ì†ë®‰ï§?/div><div class="mc-val" style="font-size:16px;">{{ qra_data.next_qra }}</div><div class="mc-sub neu">?ë¸Œì‘¨??ç­Œâ–³ë«????ë¥ë’„ ?„ì†ë®‰ï§?/div></div>
        <div class="mc"><div class="mc-lbl">ç­Œã…¼ë®??T-Bill ?„ì†ë®‰ï§‘?(30??</div><div class="mc-val">{{ qra_data.tbill_30d }}</div><div class="mc-sub neg">??«ë”…ì§?????‹ë•¾??/div></div>
        <div class="mc"><div class="mc-lbl">ç­Œã…¼ë®???ë¬’ì¥š?·ìš¸???„ì†ë®‰ï§‘?(30??</div><div class="mc-val">{{ qra_data.coupon_30d }}</div><div class="mc-sub neg">NL ?ï§ï½Œëº??/div></div>
        <div class="mc"><div class="mc-lbl">ç­Œã…¼ë®??TIPS ?„ì†ë®‰ï§‘?(30??</div><div class="mc-val">{{ qra_data.tips_30d }}</div><div class="mc-sub neu">?? ëˆŠ???¨ëº£ì§?/div></div>
        <div class="mc"><div class="mc-lbl">???????¾ë?ì»??(BTC)</div><div class="mc-val">{{ qra_data.avg_btc }}</div><div class="mc-sub neu">ç­Œã…¼ë®??30???????/div></div>
        <div class="mc"><div class="mc-lbl">???„ì†ë®‰ï§‘?(30??</div><div class="mc-val">{{ qra_data.total_30d }}</div><div class="mc-sub neg">??ë½°ì‚¢ ???‹ë•¾ ?Ÿë?ë®???/div></div>
      </div>

      <!-- ?„ì†ë®‰ï§‘???Œë”„????-->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#f87171;"></span>?????„ì†ë®‰ï§‘???Œë”„??(ç­Œã…¼ë®??30??å¤???«ë”…ì§?????‹ë•¾)
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
          * ?????„ì†ë®‰ï§‘???TGA ??«ë¡®ë¿???NL ?¶ì…ÅŠ?? T-Bill ?è¢â‘¼???„ì†ë®‰ï§‘???MMF(RRP)????¨ëªƒ????ï½‹ê¶¢ ???±ë²‰.
        </div>
      </div>

      <!-- QRA ???ì¦??«ê¿¸?? -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#60a5fa;"></span>QRA ??«ë”…ì§?????ì¦??«ê¿¸??</div>
        <div class="dts-row"><span class="dts-name">T-Bill ???´å¤·??èª˜ã‚Œë²?/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">RRP????¨ëªƒ??/span><span class="qra-tag tag-in">NL é¤“Î»ìµ????«ë¡®ë¿?/span></div>
        <div class="dts-row"><span class="dts-name">?ë¬’ì¥š?·ìš¸?????´å¤·??èª˜ã‚Œë²?/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">????é¤“Î’Â€????????‹ë•¾</span><span class="qra-tag tag-out">NL ?ï§ï½Œëº?/span></div>
        <div class="dts-row"><span class="dts-name">ç­Œâ–³ë«???Ÿë?ë®??????§’??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?«ë€€ë§©å¼›????™Â€?/span><span class="qra-tag tag-out">NL ??ë¡?µ­ ??«ë¥ê¹?/span></div>
        <div class="dts-row"><span class="dts-name">ç­Œâ–³ë«???Ÿë?ë®??????§’??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?è¢â‘¥ì¶????</span><span class="qra-tag tag-in">NL ???±ì Ÿ ??«ë¥ê¹?/span></div>
        <div class="dts-row"><span class="dts-name">?ë´”Â€ç­??µé‡‰????¾ë?ë§?é¤?/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ???ì¶?ç­Œì™–???/span><span class="qra-tag tag-in">NL ?ï§ê¾©ë§????¨ëªƒë±?/span></div>
        <div class="dts-row"><span class="dts-name">?ë´”Â€ç­??µé‡‰????ê³·êº– ??/span><span class="dts-amt" style="color:rgba(255,255,255,0.3);font-size:11px;">TGA ?????/span><span class="qra-tag tag-out">NL ?«ë€€ë§???è¢ã‚‹??/span></div>
      </div>

      <!-- ?„ì†ë®‰ï§???ê¹†ì Ÿ -->
      <div class="dts-card" style="margin-bottom:10px;">
        <div class="dts-hd"><span class="dts-dot" style="background:#fbbf24;"></span>QRA ?„ì†ë®‰ï§???ê¹†ì Ÿ (2026)</div>
        <div class="qra-pill-row">
          {% for q in qra_data.schedule %}
          <span class="qra-pill {{ 'hl' if q.current else '' }}">{{ q.label }}</span>
          {% endfor %}
        </div>
        <div style="font-size:10px;color:rgba(255,255,255,0.2);margin-top:8px;">
          TBAC ?„ì†ë®‰ï§??è«?€????ë½°ì‚¢ ?°ê¶°????‡ì‰ ?…ëš¯??? ç­Œâ–³ë«???Ÿë?ë®??????«ë€€????å¤?NL???ï§ï½Œ??
        </div>
      </div>

      <!-- ç­Œã…¼ë®???ê»‹?????ê³·ì—´ -->
      <div class="section-title" style="margin-top:4px;">ç­Œã…¼ë®???ê»‹?????ê³·ì—´ (30??
        <a class="src-link" href="https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&dateFieldName=auctionDate&startDate={{ qra_data.start_date }}" target="_blank">???ê¶???/a>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th style="text-align:left;">?ê»‹????/th>
            <th style="text-align:left;">??«êµï§?/th>
            <th style="text-align:left;">ç­Œë¾?¡ç”±?/th>
            <th>?„ì†ë®‰ï§‘??B)</th>
            <th>??¾ë?ì»??/th>
            <th>?«ë€€?????«ë¡®ëµ??/th>
          </tr></thead>
          <tbody>
            {% for r in qra_data.auctions %}
            <tr>
              <td style="text-align:left;">{{ r.date }}</td>
              <td style="text-align:left;">
                <span class="has-tip" style="font-size:11px;padding:1px 7px;border-radius:4px;background:{{ r.type_bg }};color:{{ r.type_color }};"
                  data-tip-title="{{ r.tip_title }} å¤?{{ r.term }}"
                  data-tip-body="{{ r.tip_body }}"
                  data-tip-liq="{{ r.tip_liq }}"
                  data-tip-neg="{{ 'true' if r.tip_neg else 'false' }}">{{ r.stype }}</span>
              </td>
              <td style="text-align:left;color:rgba(255,255,255,0.4);">{{ r.term }}</td>
              <td>{{ r.amt }}</td>
              <td>
                <span class="{{ 'badge-up' if r.btc_ok else 'badge-dn' }} has-tip"
                  data-tip-title="??¾ë?ì»??(Bid-to-Cover)"
                  data-tip-body="?ê»‹??????¾ì»³ ??ë½±ë€??ï§????‡ì»³?? ??ë¥ë’„ ?¶ì…ë² çŒ·?ç­Œì™–???"
                  data-tip-liq="{{ '2.3x????ë¥ë’„ ??¾ë³¦ê¹? if r.btc_ok else '2.3x????ë¥ë’„ ?ë´”Â€???ê»‹??? }}"
                  data-tip-neg="{{ 'false' if r.btc_ok else 'true' }}">{{ r.btc }}</span>
              </td>
              <td>
                <span class="has-tip" style="color:rgba(255,255,255,0.5);"
                  data-tip-title="???‡ì»³ ?«ë€€?????«ë¡®ëµ??
                  data-tip-body="{{ 'T-Bill: ??«ë¡®ëµ??Discount Rate) ?«ê¿¸??. ?èª˜ã‚Œë±??ë¡?¨¯ ??ï½‹â”› ???????????' if r.is_bill else '?ë¬’ì¥š?·ìš¸?? ç­Œã…¼ë®?????‡ì»³ ??ë¥ëµ¡??High Yield). ?èª˜ã‚Œë±??ë¡?¨¯ ??????ê³¸ì½ ?ë´”Â€???¸ê¼¤ å¤?NL ?è§€???ï§ï½Œëº?' }}"
                  data-tip-liq="{{ '??ï½‹â”›?«ë€€????„ì»ë«šå ‰??ç­Œì™–??? if r.is_bill else '?è§€?±ê²½ë¬??¿ë´º?????…ëš¯???ç­Œë º???¨ì¢Š???ï§ï½Œëº? }}"
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

  <div class="section-title">???????æº??ç­?ê¼???    <a class="src-link" href="https://www.irs.gov/businesses/small-businesses-self-employed/tax-calendar" target="_blank">IRS Calendar ??/a>
  </div>
  <div class="chart-card" style="padding:14px 16px;margin-bottom:12px;">
    <div class="cal-legend">
      <span><span class="cal-legend-dot" style="background:#34d399;"></span>??«ë”…ì§????«ë¡®ë¿?(???ë‹‹å¤?ï§?ç­Œì™–???</span>
      <span><span class="cal-legend-dot" style="background:#f87171;"></span>??«ë”…ì§????«ë¡°??(?ï§ëª…????å¤·ëš§ë¤??„ë…ì³??«ë»¬)</span>
      <span><span class="cal-legend-dot" style="background:rgba(255,255,255,0.2);"></span>é¤“Î»ìµ???„ì†ë®‰ï§?/span>
    </div>
    <div class="cal-grid">
      <div class="cal-m"><div class="cal-mn">1??/div>
        <span class="cal-ev ev-out">4Q ?ê³•ë—„?????? (1/15)</span>
        <span class="cal-ev ev-neu">IRS ??«ë²???ë½°ã‚µ ?¶ì†ë®??/span>
        <span class="cal-ev ev-in">????ˆí‰ª?ê³¸ì‚¢å¤·ëš³ì­–éº?œíƒµ??ˆÂ€???¸ê¼¤</span>
        <span class="cal-ev ev-neu">QRA ?„ì†ë®‰ï§?~1/29)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">2??/div>
        <span class="cal-ev ev-in">???ë‹‹ ??? ì¾¿ (W-2)??¾ë‚…ê¼?/span>
        <span class="cal-ev ev-in">EITCå¤·ë˜‚TC ???ë‹‹ ?¶ì†ë®??/span>
        <span class="cal-ev ev-in">????ˆí‰ª?ê³¸ì‚¢å¤·ëš³ì­–éº?œíƒµ??ˆÂ€???¸ê¼¤</span>
        <span class="cal-ev ev-neu">H.4.1 ç­Œë²?‰ç«Š???ë¥ë’„??/span>
      </div>
      <div class="cal-m"><div class="cal-mn">3??/div>
        <span class="cal-ev ev-in">???ë‹‹ ç­Œì™–????ê¼?/span>
        <span class="cal-ev ev-neu">S-Corpå¤??ê³•ë±œ???°ë¼ ??«ë²??3/15)</span>
        <span class="cal-ev ev-neu">T-Note ?ë¸Œì‘¨?±ê³„ì³??«ë»¬</span>
        <span class="cal-ev ev-out">????ç­Œë¾?¡ç”±ê³—ì??¥â–²???¸ë¦°è¢ì™ê¼?/span>
      </div>
      <div class="cal-m hl-red"><div class="cal-mn red">4????/div>
        <span class="cal-ev ev-out">Tax Day (4/15)?è¹‚ì…ê¼?/span>
        <span class="cal-ev ev-out">1Q ?ê³•ë—„???(4/15)??/span>
        <span class="cal-ev ev-out">TGA ?«ë€€ë§©å¼›???NL ?¶ì…ÅŠ??/span>
        <span class="cal-ev ev-neu">??¨ì€¬ì‚¢??«ë¡ª??Form 4868)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">5??/div>
        <span class="cal-ev ev-in">??ºìš©?????ë‹‹ ç­Œì™–????ê¼?/span>
        <span class="cal-ev ev-neu">Form 990 ????ë¸€ëµ???«ë²??/span>
        <span class="cal-ev ev-in">?ï§? ç­Œì™–????ï§ã…ºë§??ºë?ê¼?/span>
        <span class="cal-ev ev-neu">QRA ?„ì†ë®‰ï§?~4??ºì– ì¶?</span>
      </div>
      <div class="cal-m"><div class="cal-mn">6??/div>
        <span class="cal-ev ev-out">2Q ?ê³•ë—„???(6/15)??/span>
        <span class="cal-ev ev-in">??åª›ë¬’ì®?ï§ë‚…???ç­Œì™–??ê³—ë???/span>
        <span class="cal-ev ev-neu">T-Bill ?ï§????¥â–²???¸ë¦°?/span>
        <span class="cal-ev ev-neu">FOMC ???ë²????ë§?</span>
      </div>
      <div class="cal-m"><div class="cal-mn">7??/div>
        <span class="cal-ev ev-in">????ˆí‰ª?ê³¸ì‚¢ ç­Œì™–??«ë€€ë§??/span>
        <span class="cal-ev ev-in">ç­Œë¡«?€?¾ë???ˆÂ€??ë¶¾ï¼˜ï§??????è«??/span>
        <span class="cal-ev ev-in">??å·??ï§ë‚…???ç­Œì™–??ê³—ë???/span>
        <span class="cal-ev ev-neu">QRA ?„ì†ë®‰ï§?~7/28)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">8??/div>
        <span class="cal-ev ev-out">T-Bill ???Ÿë?ë®???„ì†ë®‰ï§‘??/span>
        <span class="cal-ev ev-neu">QRAå¤·ë˜–BAC ?„ì†ë®‰ï§?/span>
        <span class="cal-ev ev-in">?ï§? ????µì???ê³—ë???/span>
        <span class="cal-ev ev-neu">????? ??¨ì€ªí¬(???)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">9??/div>
        <span class="cal-ev ev-out">3Q ?ê³•ë—„???(9/15)??/span>
        <span class="cal-ev ev-in">??????¨ëº£ì¦?ç­Œë¾?ƒè€?ç­Œì™–??ê³—ë????/span>
        <span class="cal-ev ev-out">?????ë¸Œì‘¨???„ì†ë®‰ï§‘??/span>
        <span class="cal-ev ev-neu">??????¨ëº£ì¦???«êµï§?9/30)</span>
      </div>
      <div class="cal-m"><div class="cal-mn">10??/div>
        <span class="cal-ev ev-neu">????????¨ëº£ì¦??¶ì†ë®??FY)</span>
        <span class="cal-ev ev-neu">??¨ì€¬ì‚¢ ç­Œë¾?ƒè€?10/15)</span>
        <span class="cal-ev ev-in">????ˆí‰ª?ê³¸ì‚¢ COLA ?ï§ê¾§ë§??/span>
        <span class="cal-ev ev-neu">TIC ??¨ì€¬ëµ ???„ì†ë®‰ï§?~18??</span>
      </div>
      <div class="cal-m"><div class="cal-mn">11??/div>
        <span class="cal-ev ev-in">??¨ëº¤ì¶??ï§? ç­Œì™–??ê³—ë???/span>
        <span class="cal-ev ev-in">????ˆí‰ª?ê³¸ì‚¢å¤·ëš©???ç­Œì™–??ê³—ë???/span>
        <span class="cal-ev ev-neu">QRA ?„ì†ë®‰ï§?~10??ºì– ì¶?</span>
        <span class="cal-ev ev-neu">T-Bond ?ë¸Œì‘¨?±ê³„ì³??«ë»¬</span>
      </div>
      <div class="cal-m hl-green"><div class="cal-mn green">12????/div>
        <span class="cal-ev ev-in">ç­Œì™–?????? ì¾¿??¾ë‚…ê¼?(????ë¡³ì­•??—ë¹µ)</span>
        <span class="cal-ev ev-out">??¨ëº¤ì¶??ï§ëª…???????/span>
        <span class="cal-ev ev-in">????ˆí‰ª?ê³¸ì‚¢ ????«ë€€ë§??/span>
        <span class="cal-ev ev-neu">??? ç­Œã…¼ë®‡ä¼Š?FOMC</span>
      </div>
    </div>
  </div>

  <details class="collapsible">
    <summary>??ë½°ì‚¢ ??«ë”…ì§???«ê¿¸?? <a class="src-link" href="https://www.federalreserve.gov/releases/h41/" target="_blank" onclick="event.stopPropagation()">H.4.1 ??/a></summary>
    <div class="collapsible-body">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:12px;line-height:1.8;">
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">?è«???«ë”…ì§????«ë¡®ë¿???«ë¥ê¹?(NL ??¨ëªƒë±??ºê³Œ?—æ´?</div>
          <div class="dts-row"><span class="dts-name">WALCL ç­Œì•¹ë¹?</span><span style="color:#34d399;font-size:11px;">Fed ?????ç­Œë²???????ë½°ã‰¦ ??????/span></div>
          <div class="dts-row"><span class="dts-name">TGA ?¶ì…ÅŠ??/span><span style="color:#34d399;font-size:11px;">??ï¤?ë´”Â€ ç­Œì™–?????????é¤“Î’Â€??????/span></div>
          <div class="dts-row"><span class="dts-name">RRP ?¶ì…ÅŠ??/span><span style="color:#34d399;font-size:11px;">MMF ???????ë½°ì‚¢ ??«ë¡®ë¿??/span></div>
          <div class="dts-row"><span class="dts-name">?ë´”Â€ç­??µé‡‰????¾ë?ë§?/span><span style="color:#34d399;font-size:11px;">TGA ???ì¶???NL ?«ë€€ë§©æ¹²??/span></div>
          <div class="dts-row"><span class="dts-name">QE ????/span><span style="color:#34d399;font-size:11px;">WALCL ?ï§? ??ç­ŒìšŠ?????«ë”…ì§?æ¹²ê³•ê¼?/span></div>
          <div class="dts-row"><span class="dts-name">???ë‹‹ ??ë½°ã‚µ (2~3??</span><span style="color:#34d399;font-size:11px;">TGA ?¶ì…ÅŠ??±ì???????/span></div>
          <div class="dts-row"><span class="dts-name">SRFå¤?ï§ã…¼??????/span><span style="color:#34d399;font-size:11px;">Fed ?«ë€€?????«ë”…ì§????¤ë????/span></div>
          <div class="dts-row"><span class="dts-name">?ï§ë‚†?ç™°ê·£ë˜»?? ??????ë¡?²’</span><span style="color:#34d399;font-size:11px;">??ê³¸ë‡š é¤“Î»ìµ????????±ë„??ê¹†ëµ¥??/span></div>
        </div>
        <div>
          <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">?è«???«ë”…ì§????«ë¡°????«ë¥ê¹?(NL ??ë¡?µ­ ?ºê³Œ?—æ´?</div>
          <div class="dts-row"><span class="dts-name">WALCL ?¶ì…ÅŠ??(QT)</span><span style="color:#f87171;font-size:11px;">Fed ??????ê³•ë²¡????é¤“Î’Â€??????¶ì…ÅŠ???/span></div>
          <div class="dts-row"><span class="dts-name">TGA ?«ë€€ë§©å¼›?/span><span style="color:#f87171;font-size:11px;">?ï§ëª…????å¤·ëš§ë¤??„ë…ì³??«ë»¬ ????ë½°ã‰¦ ???‹ë•¾??/span></div>
          <div class="dts-row"><span class="dts-name">RRP ç­Œì•¹ë¹?</span><span style="color:#f87171;font-size:11px;">MMF?¶ì›? Fed??????????²ë’„??/span></div>
          <div class="dts-row"><span class="dts-name">Tax Day (4??</span><span style="color:#f87171;font-size:11px;">TGA ?«ë€€ë§©å¼›???NL ??ï½‹â”› ?ï§ï½Œëº??/span></div>
          <div class="dts-row"><span class="dts-name">?ê³•ë—„??????(?ë¸Œì‘¨??</span><span style="color:#f87171;font-size:11px;">1/15 å¤?4/15 å¤?6/15 å¤?9/15??/span></div>
          <div class="dts-row"><span class="dts-name">T-Bill ???Ÿë?ë®???„ì†ë®‰ï§‘?/span><span style="color:#f87171;font-size:11px;">??ë½°ã‰¦ ????????„ëƒ?????‹ë•¾??/span></div>
          <div class="dts-row"><span class="dts-name">?ë´”Â€ç­??µé‡‰????ê³·êº– ??/span><span style="color:#f87171;font-size:11px;">TGA ???????NL ?«ë€€ë§???/span></div>
          <div class="dts-row"><span class="dts-name">?«ê¿¸???«ë€€????ï§ê¾§ë§?/span><span style="color:#f87171;font-size:11px;">RRP ?«ë€€???ç­Œë²????????????«ë¡°???/span></div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:rgba(255,255,255,0.25);">
        ?è£?<b style="color:rgba(255,255,255,0.4);">???ë¼???¤ë²Š??</b> NL = WALCL ??TGA ??RRP &nbsp;å¤?nbsp;
        NL????¨ëªƒë±??ë¡?Šº ??ë½°ã‰¦ ??«ë”…ì§??ç­Œì•¹ë¹? ???è¢ã‚‹????????«ë¥ê¹??ê»‹???&nbsp;å¤?nbsp;
        <a href="https://fred.stlouisfed.org/series/WALCL" target="_blank" style="color:#60a5fa;text-decoration:none;">WALCL??/a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/WDTGAL" target="_blank" style="color:#60a5fa;text-decoration:none;">TGA??/a> &nbsp;
        <a href="https://fred.stlouisfed.org/series/RRPONTSYD" target="_blank" style="color:#60a5fa;text-decoration:none;">RRP??/a>
      </div>
    </div>
  </details>

  <details class="collapsible">
    <summary>??£ì‘´æ²??„ì»ë«–è‹¡?˜ì—?/summary>
    <div class="collapsible-body">
      <div class="method-box" style="margin-bottom:0;">
        <h3>1. Net Liquidity</h3>
        <div class="formula">NL = WALCL ??TGA ??RRP</div>
        <div class="desc"><b>WALCL</b>: Fed ?Î¼ë¹?????ç­Œë¾????ë¡?¨¯ ??ë½°ã‰¦?????±ëµ  ç­Œë¾?????????¨ë°´ë¬?/div>
        <div class="desc"><b>TGA ç­Œâ–³ëª¿è€?/b>: ??ï¤?ë´”Â€?¶ì›? Fed?????²ë’„???è¢ã‰??????ë½°ì‚¢?????ê·? ??? ??/div>
        <div class="desc"><b>RRP ç­Œâ–³ëª¿è€?/b>: MMF ?æºë†ëµ?Fed??ç­Œë¯?„ç”±????????ºìš©ë§?????ë½°ì‚¢ ?„ì…?¼è‚‰????ˆë®‰ ??/div>
        <div class="desc" style="margin-top:6px;">??Michael Howell(CrossBorder Capital), Lyn Alden ?æºë†ëµ???é¤“Î»ìµ‘?? Fed ??«ë”…ì§?æºë†ëµ????±ì «????ë½°ì‚¢????ê³—ì¶³???????±ë¿³??? ç­ŒÎ²ë¦??</div>
        <h3 style="margin-top:14px;">2. NL ??? ??¤ë²Š??¿ì²???/h3>
        <div class="formula">SPX_FV = slope ??NL + intercept</div>
        <div class="desc">2000?è¢????è¢â‘¹?ºç¹¹ë¨?Š£? ???¥í¢ ??¨ì€¬ëµ ??¨ëº¤ì¨???«ë¥êµ???. NL????SPX ??¤ë²Š??¿ì²??ì‚´ê¼????¨Â€??ç­Œë¤´ë«€??™ì­•?</div>
        {% if model_info %}<div class="model-info">slope={{ model_info.slope }} | intercept={{ model_info.intercept }} | Rï§?{{ model_info.r2 }} | n={{ model_info.n }}</div>{% endif %}
        <h3 style="margin-top:14px;">3. ??????/h3>
        <div class="formula">??????= (SPX?è¢â‘¹?ºæ¶?›Â€ ??FV) / FV ??100 (%)</div>
        <div class="desc">??¾ë???+): ??¥ì¥š?·ë“¿ì²? &nbsp;|&nbsp; ???????: ?????</div>
        <div class="warn">??NL??Šë»‡X ?????¨Â€??Rï§??.6~0.8)????ëº£ê¶š ?«ê¿¸?—è€????ë¤µÂ€???? ?ï§ëªƒê¶??¨Â€??£ì‘¨? ?è¢â‘¤ë¹??????¨Â€??£ì‘´????ˆë¼„. ?????FV?°ê·£???<b>?„ì»ë«šå ‰??´ìš©ï¼??˜ëµ³??ê³•ë—„??/b> ?è¢â‘¼?’åš¥???ë½°ë’  äº?‚…???</div>
      </div>
    </div>
  </details>

  <div class="section-title">??ºìš©??/div>
  <div class="summary-box">
    <div class="row"><span class="lbl">?«ê¿¸????/span><span class="val">{{ summary.base_date }}</span></div>
    <div class="row"><span class="lbl">WALCL ({{ summary.walcl_date }})</span><span class="val">{{ summary.walcl_raw }}</span></div>
    <div class="row"><span class="lbl">TGA ({{ summary.tga_date }})</span><span class="val">{{ summary.tga_raw }}</span></div>
    <div class="row"><span class="lbl">RRP ({{ summary.rrp_date }})</span><span class="val">{{ summary.rrp_raw }}</span></div>
    <div class="row"><span class="lbl">Net Liquidity</span><span class="val {{ 'pos' if summary.nl_chg_pos else 'neg' }}">{{ summary.nl_raw }} &nbsp;({{ summary.nl_chg }})</span></div>
    <hr class="divider">
    <div class="row"><span class="lbl">NL ??? ??¤ë²Š??¿ì²???/span><span class="val">{{ summary.fv_nl }}</span></div>
    <div class="row"><span class="lbl">SPX ?è¢â‘¹?ºæ¶?›Â€</span><span class="val {{ 'pos' if summary.fv_nl_cheap else 'neg' }}">{{ summary.spx_raw }} &nbsp;({{ summary.fv_nl_gap }})</span></div>
  </div>

  <div class="section-title">ç­Œã…¼ë®??10 ??¨ëªƒ?????¨ì€¬ëµ ??/div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>??«ë¡®?</th><th style="text-align:right;">WALCL(B)</th><th style="text-align:right;">TGA(B)</th><th style="text-align:right;">RRP(B)</th><th style="text-align:right;">Net Liq(B)</th><th style="text-align:right;">DoD</th><th style="text-align:right;">SP500</th><th style="text-align:right;">NL FV</th><th style="text-align:right;">??????/th></tr></thead>
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
  <div class="error">TIC ??¨ì€¬ëµ ?????´ì²’: {{ tic_error }}</div>
{% elif not tic_chart_html %}
  <div class="loading">TIC ??¨ì€¬ëµ ???¥â‰ªë®†é€?é¤?..</div>
{% else %}

  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">?…ëš¯??ê·ë¤ƒ?æ²ƒì„???‰ã–??°ê·£???????Monthly (2000?è«?Ÿ•esent)
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

  <div class="section-title">ç­Œã…¼ë®???°ê·£???????ë½°ë§„ <span style="font-weight:400;color:rgba(255,255,255,0.2);font-size:10px;">{{ tic_updated_at }} ?«ê¿¸?? å¤???6???è¢ã‚‹ë»??„ì†ë®‰ï§?/span></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>#</th><th>???</th><th style="text-align:right;">?°ê·£?????(B)</th><th style="text-align:right;">?è¢â‘¹?ç™²?/th><th style="text-align:right;">???´å¤·?/th></tr></thead>
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
    <b style="color:#cc0000;">TIC ??¨ì€¬ëµ ????</b><br>
    Treasury International Capital ??æ²???ï¤?ë´”Â€?¶ì›? ç­Œë²????„ì†ë®‰ï§??ë¡«ë®‰ ?ï§ëªƒ??ï§ê¾©ë²?æ²ƒì„???‰ã–??°ê·£??? ?è¢ã‚Œ?? é¤“Î»ìµŒ??†ì???ê³•ê¶š???°ê·£??????°ê¶°???ºì–œë®???????ï½ë€???æ²ƒì„???‰ã–??«ë€€??????¨ë°¸???æ²ƒì„ì±??????ë¼?ç­Œì™–???<br><br>
    <b style="color:#555;">?„ì†ë®‰ï§???ê¹†ì Ÿ (ç­Œë²???18???£í¾):</b><br>
    &nbsp;å¤?1????¨ì€¬ëµ ????3??18???„ì†ë®‰ï§?br>
    &nbsp;å¤?2????¨ì€¬ëµ ????4??18???„ì†ë®‰ï§?br>
    &nbsp;å¤?3????¨ì€¬ëµ ????5??18???„ì†ë®‰ï§?br>
    &nbsp;å¤?<i>??ê¾¨ë¦­ ???‰ëµ¬ ????æ¹???6???è¢ã‚‹ë»?/i><br><br>
    <b style="color:#555;">?…ëš¯???</b> ?°ê·£?????? custodian ?«ê¿¸?? ??é¤“Î»ìµŒ???????? ?•ê²¸ëª¿ç”±??????ê¹…í“  ???„ë§’ ???•ê²¸ëª¿ç”±???ì¨?ç­ŒìšŒìµ?? ?ë£ë«—??ºê²«???•ë˜¾å¯ƒëº¤ì®??ˆÂ€?????¼ì??•ê²¸ëª¿ç”±?????«ë€€??????Šë‹???èª? ??ë¥‚ë’„?????±ì « ?????ë¤????è¢â‘¤ë¹????????????¶ì›???œè‹‘???èª˜ã‚Œë²?
  </div>

{% endif %}
</div>

  <div class="footer">
    Net Liquidity: <a href="https://fred.stlouisfed.org" target="_blank" style="color:#60a5fa;text-decoration:none;">FRED</a> (WALCLå¤·ë˜šDTGALå¤·ë˜“RPONTSYDå¤·ë˜•P500) &nbsp;|&nbsp;
    TGA ????™ã—? <a href="https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/" target="_blank" style="color:#60a5fa;text-decoration:none;">fiscaldata.treasury.gov</a> &nbsp;|&nbsp;
    ?????æ²ƒì„???‰ã–? <a href="https://home.treasury.gov/data/treasury-international-capital-tic-system" target="_blank" style="color:#60a5fa;text-decoration:none;">U.S. Treasury TIC</a> &nbsp;|&nbsp; 2000?è«?Ÿ•esent
  </div>
</div>
</body>
</html>
"""

