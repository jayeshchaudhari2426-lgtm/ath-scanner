"""
ATH Breakout Scanner — hammer & shooting star on daily and weekly charts (Nifty 500).

Run:  python scan/scan.py
Writes site/data.json (what the website shows) and updates positions.json (the trade log).

Strategy rules (keep in sync with the "How signals are decided" section of site/index.html):
  Hammer        lower wick >= 2x body and >= 60% of range, upper wick <= 15% of range,
                range >= 0.8 x ATR(14), printed at a fresh 5-bar low. Valid while price
                stays above the hammer low.
  Shooting star mirror image at a fresh 5-bar high. Within 5% of the prior ATH = rejection -> AVOID.
  Prior ATH     highest high before the last 10 sessions.
  BUY           valid hammer (daily or weekly) + daily close above prior ATH within the last
                10 sessions, breakout volume >= 1.5x 50-day avg, breakout close in top 40% of
                its candle, close <= 5% above ATH, stop <= 10% below entry.
  Levels        entry = signal-day close, stop = 0.5% below hammer low, target = entry + 3 x risk.
  WAIT          valid hammer, price within 10% below ATH, no breakout close yet.
"""
import io, json, sys, time, urllib.request
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "site" / "data.json"
POSITIONS = ROOT / "positions.json"
INTRADAY_LOG = ROOT / "intraday_trades.json"
UNIVERSE_CACHE = ROOT / "scan" / "nifty500.csv"
UNIVERSE_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]

MAX_RISK, MAX_EXT, VOLX, NEAR, STAR_ZONE = 0.10, 0.05, 1.5, 0.10, 0.95
D_LOOKBACK, W_LOOKBACK = 10, 3
MIN_STOCKS = 400               # fail the run (keep yesterday's site) if the download comes back thin
LIVE_START = "2026-11-02"      # dummy 1-share trades are logged live from this date (positions.json)
BACKTEST_START = "2023-11-01"  # signals before LIVE_START are replayed from here


# ---------- data ----------
def load_universe():
    for url in UNIVERSE_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=30).read().decode()
            df = pd.read_csv(io.StringIO(raw))
            if {"Symbol", "Company Name"} <= set(df.columns) and len(df) > 400:
                UNIVERSE_CACHE.write_text(raw)
                print(f"Universe: {len(df)} stocks from {url}")
                return df
        except Exception as e:
            print(f"Universe download failed ({url}): {e}")
    df = pd.read_csv(UNIVERSE_CACHE)
    print(f"Universe: {len(df)} stocks from cached list")
    return df


def fetch_prices(symbols):
    tickers = [s + ".NS" for s in symbols]
    out = {}

    def grab(batch):
        d = yf.download(batch, period="max", auto_adjust=False, group_by="ticker",
                        progress=False, threads=True)
        for t in batch:
            try:
                df = d[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df):
                    out[t] = df
            except Exception:
                pass

    for i in range(0, len(tickers), 50):
        grab(tickers[i:i + 50])
    missing = [t for t in tickers if t not in out]
    if missing:  # one retry for transient failures
        time.sleep(5)
        for i in range(0, len(missing), 25):
            grab(missing[i:i + 25])
    print(f"Prices: {len(out)}/{len(tickers)} stocks")
    return out


# ---------- patterns ----------
def candle_flags(df):
    o, h, l, c = df.Open, df.High, df.Low, df.Close
    rng = h - l
    body = (c - o).abs()
    up = h - np.maximum(o, c)
    lo = np.minimum(o, c) - l
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    big = rng >= 0.8 * tr.rolling(14).mean().shift()
    hammer = (lo >= 2 * body) & (lo >= 0.6 * rng) & (up <= 0.15 * rng) & big & (l <= l.shift(1).rolling(5).min())
    star = (up >= 2 * body) & (up >= 0.6 * rng) & (lo <= 0.15 * rng) & big & (h >= h.shift(1).rolling(5).max())
    return hammer, star


def to_weekly(df):
    w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()
    w.index = w.index - pd.Timedelta(days=4)  # label each week by its Monday
    return w


def bars(df):
    return [[d.strftime("%Y-%m-%d"), round(r.Open, 2), round(r.High, 2), round(r.Low, 2),
             round(r.Close, 2), int(r.Volume)] for d, r in df.iterrows()]


def analyse(sym, name, industry, df, asof, last_week_mon):
    w_all = to_weekly(df)
    w = w_all[w_all.index <= last_week_mon]  # completed weeks only
    hd, sd = candle_flags(df)
    hw, sw = candle_flags(w)
    pats = []
    for flags, tf, kind, src, n in [(hd, "D", "Hammer", df, D_LOOKBACK), (sd, "D", "Shooting star", df, D_LOOKBACK),
                                    (hw, "W", "Hammer", w, W_LOOKBACK), (sw, "W", "Shooting star", w, W_LOOKBACK)]:
        recent = flags.tail(n)
        for d in recent[recent].index:
            pats.append(dict(date=d.strftime("%Y-%m-%d"), tf=tf, kind=kind,
                             low=round(src.loc[d].Low, 2), high=round(src.loc[d].High, 2)))
    if not pats:
        return None

    vol50 = df.Volume.rolling(50).mean().shift()
    ath = df.High.iloc[:-D_LOOKBACK].max()
    ath_date = df.High.iloc[:-D_LOOKBACK].idxmax()
    close = df.Close.iloc[-1]
    pct = close / ath - 1
    turnover = (df.Close * df.Volume).tail(50).mean() / 1e7  # Rs crore

    hams = [p for p in pats if p["kind"] == "Hammer"]
    stars = [p for p in pats if p["kind"] == "Shooting star"]
    for p in hams:
        end = pd.Timestamp(p["date"]) + (pd.Timedelta(days=4) if p["tf"] == "W" else pd.Timedelta(0))
        after = df.Low[df.index > end]
        p["held"] = bool(len(after) == 0 or after.min() >= p["low"])
    valid = [p for p in hams if p["held"]]

    last = df.tail(D_LOOKBACK)
    above = last[last.Close > ath]
    bo = None
    if len(above):
        d = above.index[0]
        r = df.loc[d]
        bo = dict(date=d.strftime("%Y-%m-%d"), volx=round(float(r.Volume / vol50.loc[d]), 2),
                  closepos=round(float((r.Close - r.Low) / (r.High - r.Low)), 2) if r.High > r.Low else 1.0,
                  low=round(r.Low, 2))

    sig, reason, status, conf = "No setup", "", "—", "—"
    entry = stop = target = risk = None
    if valid:
        stop = round(min(p["low"] for p in valid) * 0.995, 2)
        tfs = {p["tf"] for p in valid}
        if bo and close > ath:
            status = "Confirmed"
            entry = round(close, 2)
            risk = entry - stop
            checks = [("weak breakout volume", bo["volx"] >= VOLX), ("weak breakout close", bo["closepos"] >= 0.6),
                      ("more than 5% above ATH", pct <= MAX_EXT),
                      (f"stop {risk / entry * 100:.0f}% away, target impractical", risk / entry <= MAX_RISK)]
            if all(ok for _, ok in checks):
                sig = "BUY"
                conf = "High" if tfs == {"D", "W"} else "Medium"
                reason = "Hammer held, daily close above the prior ATH on volume, risk within limit."
            else:
                sig = "AVOID"
                reason = "Breakout fails the rules: " + ", ".join(m for m, ok in checks if not ok) + "."
        elif pct >= -NEAR:
            sig, status = "WAIT", "Pending"
            conf = "Medium" if "W" in tfs else "Low"
            entry = round(ath, 2)
            risk = entry - stop
            reason = f"Hammer held; needs a daily close above ₹{ath:,.2f} on volume."
            if risk / entry > MAX_RISK:
                reason += f" Stop from the hammer low is {risk / entry * 100:.0f}% away, so a breakout would still be rejected unless a tighter base forms."
        else:
            reason = f"Hammer held but price is {abs(pct) * 100:.0f}% below the ATH, too far for this strategy."
        if risk is not None:
            target, risk = round(entry + 3 * risk, 2), round(risk, 2)
    elif hams:
        sig, reason = "Invalidated", "Price broke below the hammer low after it formed."

    if stars and sig != "BUY":
        sh = max(p["high"] for p in stars)
        if close > ath and sh >= ath * STAR_ZONE and not valid:
            sig, status, conf = "Breakout (no hammer)", "Confirmed", "—"
            entry = stop = target = risk = None
            reason = "Shooting star failed and price later closed above the prior ATH. No hammer, so outside this strategy."
        elif sh >= ath * STAR_ZONE and close < sh:
            sig, conf = "AVOID", "—"
            status = "Failed" if sh >= ath else "—"
            entry = stop = target = risk = None
            reason = f"Shooting star at ₹{sh:,.2f} rejected near the ATH zone."
        elif sig == "No setup" and not hams:
            reason = "Shooting star away from the ATH zone, so no rejection at the highs."

    return dict(sym=sym, name=name, industry=industry, close=round(close, 2), ath=round(ath, 2),
                athDate=ath_date.strftime("%Y-%m-%d"), pct=round(pct * 100, 2), turnover=round(turnover, 1),
                patterns=pats, breakout=bo, signal=sig, status=status, conf=conf, reason=reason,
                entry=entry, stop=stop, target=target, risk=risk,
                daily=bars(df.tail(300)), weekly=bars(w_all.tail(260)))


# ---------- trade log ----------
def update_positions(positions, stocks, px, asof):
    for p in positions:
        if p["status"] != "Open":
            continue
        df = px.get(p["sym"] + ".NS")
        if df is None:
            continue
        after = df[df.index > pd.Timestamp(p["entryDate"])]
        for d, r in after.iterrows():
            hit_stop = r.Low <= p["stop"]
            hit_tgt = r.High >= p["target"]
            if hit_stop:  # if both trigger on one day, assume the stop came first
                p.update(status="Stop hit", exitDate=d.strftime("%Y-%m-%d"), exitPrice=round(min(r.Open, p["stop"]), 2))
                break
            if hit_tgt:
                p.update(status="Target hit", exitDate=d.strftime("%Y-%m-%d"), exitPrice=round(max(r.Open, p["target"]), 2))
                break
        p["current"] = round(float(df.Close.iloc[-1]), 2)
        ref = p.get("exitPrice") or p["current"]
        p["pnl"] = round((ref - p["entry"]) * p["qty"], 2)
        p["ret"] = round((ref / p["entry"] - 1) * 100, 2)
        p["days"] = (pd.Timestamp(p.get("exitDate") or asof) - pd.Timestamp(p["entryDate"])).days

    open_syms = {p["sym"] for p in positions if p["status"] == "Open"}
    seen = {(p["sym"], p["entryDate"]) for p in positions}
    for s in stocks:
        if asof >= LIVE_START and s["signal"] == "BUY" and s["sym"] not in open_syms and (s["sym"], asof) not in seen:
            positions.append(dict(sym=s["sym"], name=s["name"], entryDate=asof, entry=s["entry"], stop=s["stop"],
                                  target=s["target"], qty=1, conf=s["conf"], status="Open",
                                  current=s["close"], pnl=0.0, ret=0.0, days=0, exitDate=None, exitPrice=None))
            print(f"NEW BUY: {s['sym']} entry {s['entry']} stop {s['stop']} target {s['target']}")
    return positions


def main():
    uni = load_universe()
    meta = uni.set_index("Symbol")
    px = fetch_prices(list(uni.Symbol))
    if len(px) < MIN_STOCKS:
        sys.exit(f"Only {len(px)} stocks downloaded; stopping so the live site keeps yesterday's data.")

    asof = pd.Series([df.index[-1] for df in px.values()]).mode().iloc[0]
    last_fri = asof - pd.Timedelta(days=(asof.weekday() - 4) % 7)  # last completed week (ends Friday)
    last_week_mon = last_fri - pd.Timedelta(days=4)
    print(f"As of {asof.date()}, last completed week starts {last_week_mon.date()}")

    stocks, scanned = [], 0
    for t, df in px.items():
        if len(df) < 300 or df.index[-1] < asof:
            continue
        scanned += 1
        sym = t[:-3]
        row = analyse(sym, meta.loc[sym, "Company Name"], meta.loc[sym].get("Industry", ""), df, asof, last_week_mon)
        if row:
            stocks.append(row)
    order = {"BUY": 0, "WAIT": 1, "Breakout (no hammer)": 2, "AVOID": 3, "Invalidated": 4, "No setup": 5}
    stocks.sort(key=lambda x: (order[x["signal"]], -x["pct"]))

    asof_s = asof.strftime("%Y-%m-%d")
    positions = json.loads(POSITIONS.read_text()) if POSITIONS.exists() else []
    positions = update_positions(positions, stocks, px, asof_s)
    POSITIONS.write_text(json.dumps(positions, indent=1, ensure_ascii=False))

    import backtest
    names = meta["Company Name"].to_dict()
    bt = backtest.run(px, names, BACKTEST_START, LIVE_START)
    print(f"Backtest: {backtest.summary(bt)}")
    paper = dict(liveStart=LIVE_START, backtestStart=BACKTEST_START,
                 live=dict(trades=positions, summary=backtest.summary(positions)),
                 backtest=dict(trades=bt, summary=backtest.summary(bt)))
    # 5-minute version: replay the last 60 days of 5-min candles and add any new dummy trades to the log
    import intraday
    intra = dict(trades=[], summary=intraday.summary([]), funnel=None, error=None)
    try:
        wl = intraday.watchlist(px)
        p5 = intraday.fetch_5m(wl)
        new, funnel = intraday.run(px, p5, names)
        log = json.loads(INTRADAY_LOG.read_text()) if INTRADAY_LOG.exists() else []
        have = {(t["sym"], t["date"]) for t in log}
        added = [t for t in new if (t["sym"], t["date"]) not in have]
        log = sorted(log + added, key=lambda x: (x["date"], x["entryTime"]))
        INTRADAY_LOG.write_text(json.dumps(log, ensure_ascii=False))
        first5 = min((x.index[0] for x in p5.values()), default=None)
        intra = dict(trades=log, summary=intraday.summary(log), funnel=funnel,
                     window=[str(first5.date()) if first5 is not None else None, asof_s], watched=len(wl), error=None)
        print(f"Intraday: {len(p5)} stocks on 5-min, {len(added)} new trades, log {len(log)}; funnel {funnel}")
    except Exception as e:  # never let the 5-min step take the daily site down
        print(f"Intraday step failed: {e}")
        intra["error"] = str(e)
    SITE_DATA.write_text(json.dumps(dict(asof=asof_s, lastWeek=last_week_mon.strftime("%Y-%m-%d"),
                                         universe=scanned, stocks=stocks, paper=paper, intraday=intra),
                                    separators=(",", ":"), ensure_ascii=False))
    counts = pd.Series([s["signal"] for s in stocks]).value_counts().to_dict()
    print(f"Scanned {scanned}; signals: {counts}")


if __name__ == "__main__":
    main()
