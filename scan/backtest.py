"""
Paper-trade replay: applies the exact BUY rules from scan.py to every past trading day,
buying 1 dummy share whenever a BUY signal fires and holding until the stop or target is hit.

No lookahead: on each day only candles up to that day's close are used, and weekly hammers
count only once their week has closed.
"""
import numpy as np
import pandas as pd

from scan import (MAX_RISK, MAX_EXT, VOLX, D_LOOKBACK, W_LOOKBACK, candle_flags, to_weekly)


def signals_for(sym, df, start, end):
    """Yield (day_index, entry, stop, conf) for each day a BUY signal is live."""
    n = len(df)
    if n < 300:
        return
    O, H, L, C, V = (df[c].to_numpy(float) for c in ["Open", "High", "Low", "Close", "Volume"])
    dates = df.index
    vol50 = pd.Series(V).rolling(50).mean().shift().to_numpy()
    ath = pd.Series(H).cummax().shift(D_LOOKBACK).to_numpy()  # highest high before the last 10 sessions
    hd, _ = candle_flags(df)
    hd = hd.to_numpy()
    w = to_weekly(df)
    hw, _ = candle_flags(w)
    w_end = (w.index + pd.Timedelta(days=4)).to_numpy()  # Friday close of each week
    w_low = w.Low.to_numpy()
    w_ham = hw.to_numpy()
    first = max(300, int(np.searchsorted(dates.to_numpy(), np.datetime64(start))))

    last = int(np.searchsorted(dates.to_numpy(), np.datetime64(end)))  # entries before `end` only
    for i in range(first, min(n, last)):
        a = ath[i]
        if not (C[i] > a) or C[i] / a - 1 > MAX_EXT:
            continue
        # breakout = first close above ATH in the last 10 sessions
        win = np.arange(i - D_LOOKBACK + 1, i + 1)
        above = win[C[win] > a]
        b = above[0]
        rng = H[b] - L[b]
        if V[b] / vol50[b] < VOLX or (rng > 0 and (C[b] - L[b]) / rng < 0.6):
            continue
        # hammers that are still valid today
        lows, tfs = [], set()
        for j in win[hd[win]]:
            if j == i or L[j + 1:i + 1].min() >= L[j]:
                lows.append(L[j]); tfs.add("D")
        t = dates[i].to_datetime64()
        done = np.where(w_end <= t)[0][-W_LOOKBACK:]  # last 3 completed weeks
        for k in done[w_ham[done]]:
            after = L[(dates.to_numpy() > w_end[k]) & (np.arange(n) <= i)]
            if len(after) == 0 or after.min() >= w_low[k]:
                lows.append(w_low[k]); tfs.add("W")
        if not lows:
            continue
        stop = round(min(lows) * 0.995, 2)
        entry = round(C[i], 2)
        if (entry - stop) / entry > MAX_RISK or entry <= stop:
            continue
        yield i, entry, stop, ("High" if tfs == {"D", "W"} else "Medium")


def run(px, names, start, end, end_label="Open"):
    trades = []
    for t, df in px.items():
        sym = t[:-3]
        H, L, O, C = (df[c].to_numpy(float) for c in ["High", "Low", "Open", "Close"])
        dates = df.index
        busy_until = -1
        for i, entry, stop, conf in signals_for(sym, df, start, end):
            if i <= busy_until:  # already holding this stock
                continue
            risk = entry - stop
            target = round(entry + 3 * risk, 2)
            tr = dict(sym=sym, name=names.get(sym, sym), entryDate=dates[i].strftime("%Y-%m-%d"), entry=entry,
                      stop=stop, target=target, qty=1, conf=conf, status=end_label, exitDate=None, exitPrice=None,
                      current=round(C[-1], 2))
            exit_i = len(df) - 1
            for j in range(i + 1, len(df)):
                if L[j] <= stop:  # stop first if both hit on one day
                    tr.update(status="Stop hit", exitDate=dates[j].strftime("%Y-%m-%d"), exitPrice=round(min(O[j], stop), 2))
                    exit_i = j; break
                if H[j] >= target:
                    tr.update(status="Target hit", exitDate=dates[j].strftime("%Y-%m-%d"), exitPrice=round(max(O[j], target), 2))
                    exit_i = j; break
            ref = tr["exitPrice"] if tr["exitPrice"] is not None else tr["current"]
            tr["pnl"] = round(ref - entry, 2)
            tr["ret"] = round((ref / entry - 1) * 100, 2)
            tr["days"] = int((dates[exit_i] - dates[i]).days)
            trades.append(tr)
            busy_until = exit_i if tr["exitDate"] else len(df)
    trades.sort(key=lambda x: x["entryDate"])
    return trades


def summary(trades):
    closed = [t for t in trades if t["status"] != "Open"]
    wins = [t for t in closed if t["status"] == "Target hit"]
    losses = [t for t in closed if t["status"] == "Stop hit"]
    gain = sum(t["ret"] for t in wins)    # profit factor on % returns, so share price doesn't skew it
    loss = -sum(t["ret"] for t in losses)
    invested = sum(t["entry"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    return dict(trades=len(trades), openPnl=round(float(sum(t["pnl"] for t in trades if t["status"] == "Open")), 2), open=len(trades) - len(closed), closed=len(closed), wins=len(wins),
                losses=len(losses), winRate=round(len(wins) / len(closed) * 100, 1) if closed else None,
                pnl=round(float(pnl), 2), invested=round(float(invested), 2),
                retOnCapital=round(float(pnl / invested * 100), 2) if invested else None,
                avgRet=round(float(np.mean([t["ret"] for t in closed])), 2) if closed else None,
                profitFactor=round(float(gain / loss), 2) if loss else None,
                avgDays=round(float(np.mean([t["days"] for t in closed])), 1) if closed else None)
