"""
5-minute version of the strategy: daily ATH level + 5-minute breakout + 5-minute hammer retest.

For each stock and day D:
  1. Daily filter   level = all-time high as of yesterday's close. The stock is watched only if
                    yesterday's close was within 3% of that level.
  2. Breakout       a 5-min candle closes above the level, in the top 40% of its range.
  3. Retest hammer  after the breakout, a 5-min hammer whose low tags the level (within +/-0.5%)
                    and closes back above it. Relative volume at that moment >= 1.5x.
  4. Rejection      a 5-min shooting star that pokes above the level and closes below it, or any
                    close more than 0.5% under the level, cancels the setup for the day.
  5. Trade          buy 1 share at the next candle's open (no entries after 14:30). Stop 0.1% under
                    the hammer low; risk must be 0.25%-2% of entry. Target = entry + 3 x risk.
                    Anything still open is squared off at the 15:15 candle's close.
Net P&L deducts Zerodha intraday charges plus 0.02% slippage on each side.
"""
import numpy as np
import pandas as pd

WATCH_BAND = 0.97
RETEST_BAND = 0.005
RVOL_MIN = 1.5
MIN_RISK, MAX_RISK = 0.0025, 0.02
LAST_ENTRY = "14:30"
SQUARE_OFF = "15:15"
SLIPPAGE = 0.0002


def charges(buy, sell):
    """Zerodha equity intraday charges for 1 share (Rs)."""
    brokerage = min(20, 0.0003 * buy) + min(20, 0.0003 * sell)
    stt = 0.00025 * sell
    txn = 0.0000297 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    stamp = 0.00003 * buy
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + stt + txn + sebi + stamp + gst


def flags5(df):
    o, h, l, c = df.Open, df.High, df.Low, df.Close
    rng = h - l
    body = (c - o).abs()
    up = h - np.maximum(o, c)
    lo = np.minimum(o, c) - l
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    big = rng >= 0.8 * tr.rolling(14).mean().shift()
    hammer = (lo >= 2 * body) & (lo >= 0.6 * rng) & (up <= 0.15 * rng) & big & (l <= l.shift(1).rolling(5).min())
    star = (up >= 2 * body) & (up >= 0.6 * rng) & (lo <= 0.15 * rng) & big
    return hammer.to_numpy(), star.to_numpy()


def rvol(df):
    """Cumulative volume so far today / average cumulative volume at the same time over the prior 10 days."""
    d = df.copy()
    d["day"] = d.index.date
    d["slot"] = d.index.strftime("%H:%M")
    d["cum"] = d.groupby("day").Volume.cumsum()
    piv = d.pivot_table(index="day", columns="slot", values="cum")
    base = piv.rolling(10, min_periods=5).mean().shift(1)
    vals = [base.at[dy, sl] if sl in base.columns else np.nan for dy, sl in zip(d.day, d.slot)]
    return (d.cum / np.array(vals, dtype=float)).to_numpy()


def watchlist(daily, lookback_days=60):
    """Stocks whose previous close was within 3% of their ATH on any of the last `lookback_days` days."""
    out = []
    for t, dd in daily.items():
        if len(dd) < 300:
            continue
        near = dd.Close.shift(1) >= WATCH_BAND * dd.High.cummax().shift(1)
        if near.tail(lookback_days).any():
            out.append(t)
    return out


def fetch_5m(tickers):
    import time
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 20):
        b = tickers[i:i + 20]
        d = yf.download(b, period="60d", interval="5m", auto_adjust=False, group_by="ticker",
                        progress=False, threads=True)
        for t in b:
            try:
                x = d[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(x):
                    out[t] = x
            except Exception:
                pass
        time.sleep(1)
    return out


def run(daily, five, names):
    trades = []
    funnel = dict(watched=0, breakout=0, hammerAtLevel=0, volumeOk=0, traded=0)
    for t, m5 in five.items():
        dd = daily.get(t)
        if dd is None or len(dd) < 300 or len(m5) < 200:
            continue
        sym = t[:-3]
        m5 = m5.copy()
        m5.index = m5.index.tz_localize(None) if m5.index.tz is not None else m5.index
        ath_y = dd.High.cummax().shift(1)
        prev_close = dd.Close.shift(1)
        ham, star = flags5(m5)
        rv = rvol(m5)
        O, H, L, C = (m5[c].to_numpy(float) for c in ["Open", "High", "Low", "Close"])
        times = m5.index.strftime("%H:%M").to_numpy()
        days = m5.index.normalize()
        for day in days.unique():
            if day not in dd.index:
                continue
            level, pc = ath_y.loc[day], prev_close.loc[day]
            if not (pc >= WATCH_BAND * level):
                continue
            idx = np.where(days == day)[0]
            funnel["watched"] += 1
            broke, done, seen_h, seen_v = False, False, False, False
            for i in idx:
                if done:
                    break
                if not broke:
                    rng = H[i] - L[i]
                    if C[i] > level and (rng == 0 or (C[i] - L[i]) / rng >= 0.6):
                        broke = True
                        funnel["breakout"] += 1
                    continue
                if C[i] < level * (1 - RETEST_BAND) or (star[i] and H[i] > level and C[i] < level):
                    done = True  # breakout failed or rejected for today
                    break
                if times[i] >= LAST_ENTRY or i + 1 > idx[-1]:
                    break
                at_level = ham[i] and level * (1 - RETEST_BAND) <= L[i] <= level * (1 + RETEST_BAND) and C[i] > level
                if at_level and not seen_h:
                    seen_h = True
                    funnel["hammerAtLevel"] += 1
                if at_level and rv[i] >= RVOL_MIN:
                    if not seen_v:
                        seen_v = True
                        funnel["volumeOk"] += 1
                    entry = O[i + 1]
                    stop = round(L[i] * 0.999, 2)
                    risk = entry - stop
                    if not (MIN_RISK <= risk / entry <= MAX_RISK):
                        continue
                    target = entry + 3 * risk
                    status, exit_p, exit_t = "Squared off", None, None
                    for j in idx[idx >= i + 1]:
                        if L[j] <= stop:
                            status, exit_p = "Stop hit", min(O[j], stop) if j > i + 1 else stop
                        elif H[j] >= target:
                            status, exit_p = "Target hit", max(O[j], target) if j > i + 1 else target
                        elif times[j] >= SQUARE_OFF:
                            exit_p = C[j]
                        if exit_p is not None:
                            exit_t = m5.index[j]
                            break
                    if exit_p is None:  # data ended early
                        exit_p, exit_t = C[idx[-1]], m5.index[idx[-1]]
                    buy, sell = entry * (1 + SLIPPAGE), exit_p * (1 - SLIPPAGE)
                    gross = exit_p - entry
                    cost = charges(buy, sell) + (buy - entry) + (exit_p - sell)
                    trades.append(dict(sym=sym, name=names.get(sym, sym), date=day.strftime("%Y-%m-%d"),
                                       level=round(level, 2), entryTime=m5.index[i + 1].strftime("%H:%M"),
                                       exitTime=exit_t.strftime("%H:%M"), entry=round(entry, 2), stop=stop,
                                       target=round(target, 2), exitPrice=round(exit_p, 2), status=status,
                                       riskPct=round(risk / entry * 100, 2), rvol=round(float(rv[i]), 2),
                                       gross=round(gross, 2), cost=round(cost, 2), pnl=round(gross - cost, 2),
                                       ret=round((gross - cost) / entry * 100, 3), r=round((gross - cost) / risk, 2),
                                       hammerTime=times[i],
                                       bars=[[int(pd.Timestamp(m5.index[k]).timestamp()), round(O[k], 2), round(H[k], 2),
                                              round(L[k], 2), round(C[k], 2)] for k in idx]))
                    funnel["traded"] += 1
                    done = True  # one trade per stock per day
    trades.sort(key=lambda x: (x["date"], x["entryTime"]))
    return trades, funnel


def summary(trades):
    n = len(trades)
    if not n:
        return dict(trades=0, targets=0, stops=0, squared=0, winRate=None, pnl=0, gross=0, costs=0,
                    avgRet=None, avgR=None, profitFactor=None, avgRisk=None, days=0)
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["ret"] for t in wins)
    gl = -sum(t["ret"] for t in trades if t["pnl"] <= 0)
    by = lambda s: sum(1 for t in trades if t["status"] == s)
    return dict(trades=n, targets=by("Target hit"), stops=by("Stop hit"), squared=by("Squared off"),
                winRate=round(len(wins) / n * 100, 1), pnl=round(float(sum(t["pnl"] for t in trades)), 2),
                gross=round(float(sum(t["gross"] for t in trades)), 2), costs=round(float(sum(t["cost"] for t in trades)), 2),
                avgRet=round(float(np.mean([t["ret"] for t in trades])), 3),
                avgR=round(float(np.mean([t["r"] for t in trades])), 2),
                profitFactor=round(float(gp / gl), 2) if gl else None,
                avgRisk=round(float(np.mean([t["riskPct"] for t in trades])), 2),
                days=len({t["date"] for t in trades}))
