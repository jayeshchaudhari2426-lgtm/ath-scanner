# ATH Breakout Scanner

A website that scans all Nifty 500 stocks every trading day for **hammer** and **shooting star** candles on the daily and weekly charts. It flags all-time-high breakouts as BUY / WAIT / AVOID under a fixed 1:3 risk-reward, and keeps a running trade log.

It runs free on GitHub. Every weekday at 5:37 PM IST, GitHub reruns the scan, updates the trade log and republishes the site. No computer or server of your own is needed.

## What's in here

| Path | What it does |
|---|---|
| `site/index.html` | The website: screener, charts, signal sheet, portfolio tracker |
| `scan/scan.py` | The scanner. Downloads prices, finds patterns, decides signals, updates the trade log |
| `scan/backtest.py` | Replays the same BUY rules over past days (from 1 Nov 2023) as dummy 1-share trades |
| `scan/intraday.py` | The 5-minute version: daily ATH level, 5-min breakout, 5-min hammer retest, intraday costs |
| `intraday_trades.json` | Log of every 5-minute dummy trade, added to each evening |
| `scan/nifty500.csv` | Backup copy of the Nifty 500 list, used if NSE's site can't be reached |
| `positions.json` | The trade log. Every BUY is added here and tracked until its stop or target is hit |
| `site/config.js` | Where you paste your Cloudflare Worker address to turn on the live TradingView feed |
| `tradingview/ath_hammer_strategy.pine` | The same strategy as a TradingView Pine Script, with alerts that post trades to your Worker |
| `worker/worker.js`, `worker/schema.sql` | The free Cloudflare Worker and database that receive TradingView alerts and serve them to the site |
| `.github/workflows/scan.yml` | The daily job that runs the scan and publishes the site |

## Put it online (about 10 minutes, one time)

1. **Create a repository.** Sign in at github.com (a free account works) → **New repository** → name it, for example `ath-scanner` → set it to **Public** → **Create repository**. Free accounts can only publish sites from public repositories.
2. **Upload the files.** On the new repository's page, click **uploading an existing file** and drag in everything from this folder, then **Commit changes**.
   - The `.github` folder is hidden on most computers. On a Mac, press **Cmd + Shift + .** in Finder to show it. On Windows, turn on **View → Hidden items**.
   - If it still doesn't upload, use **Add file → Create new file**, type the name `.github/workflows/scan.yml`, paste in that file's contents and commit.
3. **Turn on the website.** Go to **Settings → Pages**, and under **Build and deployment → Source** choose **GitHub Actions**.
4. **Run the first scan.** Open the **Actions** tab, choose **Daily scan and deploy** → **Run workflow**. It takes about 3–5 minutes. A failed run from step 2 may already be listed. Ignore it: it started before Pages was turned on.
5. **Open your site** at `https://<your-username>.github.io/ath-scanner/`. The link also appears in the finished run and under Settings → Pages.

After that it updates itself every trading day. To refresh it by hand, use **Run workflow** again.

## Live feed from TradingView (optional, about 20 minutes)

This runs the strategy on TradingView's own data. TradingView sends each BUY, exit and daily price update to a small free Cloudflare service, and your website shows the trades within a minute.

**You need:** a paid TradingView plan, since webhooks aren't on the free plan, and two-factor authentication turned on in TradingView, which webhooks require. You also need a free Cloudflare account.

**1. Set up the receiver on Cloudflare (free)**
1. Sign in at dash.cloudflare.com, then go to **Storage & Databases → D1 → Create database**. Name it `ath_trades`.
2. Open the database's **Console**, paste the contents of `worker/schema.sql` and run it.
3. Go to **Workers & Pages → Create → Create Worker**, name it `ath-alerts` and deploy. Then **Edit code**, replace everything with `worker/worker.js` and deploy again.
4. In the Worker's **Settings**:
   - Under **Bindings**, add a D1 database with the variable name `DB` and choose `ath_trades`.
   - Under **Variables and Secrets**, add a **Secret** named `WEBHOOK_SECRET` with a long random value. Keep it private.
5. Note the Worker address, for example `https://ath-alerts.yourname.workers.dev`. Opening it should show `{"ok":true,...}`.

**2. Point the website at it**
- In GitHub, edit `site/config.js` and paste the Worker address into `liveApi`, then commit. The site redeploys automatically.

**3. Add the strategy to TradingView**
1. Open a stock on a **1D** chart, open the **Pine Editor**, paste `tradingview/ath_hammer_strategy.pine`, then **Save** and **Add to chart**.
   - A panel shows the signal (BUY / WAIT / AVOID / IN TRADE), the prior ATH, the stop and the breakout volume.
   - The **Strategy Tester** tab backtests the same rules on that stock with TradingView's data.
2. In the strategy's **Settings → Inputs**, set **Webhook key** to the same value as `WEBHOOK_SECRET`.
3. Create an alert (clock icon → **Create alert**):
   - **Condition:** the strategy, then **alert() function calls only**.
   - **Notifications:** tick **Webhook URL** and enter your Worker address followed by `/webhook`.
   - Leave the message box as it is; the script writes the message.
4. Repeat step 3 for each stock you want watched. Each alert covers one stock, and your plan caps how many alerts you can have. Start with the WAIT stocks and others near their all-time high from the website's screener. On Essential and Plus plans alerts expire after about two months, so renew them.

**Checking it works:** TradingView's **Alerts → Log** shows each alert it sent and whether the webhook was delivered. In Cloudflare, the `events` table in D1 keeps every message received.

**How it differs from the daily Python scan:**
- TradingView uses its own data and history, so ATH levels can differ slightly. A shorter chart history can also miss a very old ATH.
- A weekly hammer counts once the week has closed. On a Friday, the Python scan already counts that day's week, so the two can differ by a day.
- If a stop and a target are both touched on the same day, TradingView guesses the order from the candle shape. The Python scan always assumes the stop.

## Good to know

- **Price strip.** When there are WAIT stocks or open live trades, a TradingView ticker strip shows them at the top of the page. TradingView's free embeds give end-of-day BSE prices (marked "E"), not tick-by-tick prices.
- **TradingView charts.** The **Scan view** draws charts with TradingView's open-source Lightweight Charts library and marks the patterns and levels. **TradingView live** embeds TradingView's own chart widget. TradingView doesn't allow NSE data in free embeds, so that widget shows the stock's **BSE** listing. If a BSE symbol differs, you can change it in the widget's search box. **Open in TradingView** always opens the NSE chart.
- **Data source.** End-of-day NSE prices come from Yahoo Finance through the `yfinance` library. That's fine for personal use, but Yahoo's terms don't allow republishing its data publicly. If you plan to share the site widely, switch `fetch_prices()` in `scan/scan.py` to a licensed NSE data feed.
- **Paper portfolio.** Every BUY signal opens a dummy trade of 1 share at that day's close. It is held until the stop or the 1:3 target is hit. If both fall on the same day, the stop is assumed (the conservative choice). The site shows it in two views:
  - **Live:** real-time paper trades from 2 Nov 2026 onward, saved in `positions.json`. To reset them, replace that file's contents with `[]`.
  - **Backtest:** the same rules replayed on every trading day from 1 Nov 2023 to 2 Nov 2026, using only data available on each day. It runs on today's Nifty 500 members, so stocks that left the index are missing, which flatters past results.
  - Both dates are set by `LIVE_START` and `BACKTEST_START` at the top of `scan/scan.py`.
  - **Intraday 5-min:** stocks that closed within 3% of their ATH the day before are watched on the 5-minute chart. A trade needs a 5-minute close above the ATH, then a 5-minute hammer that retests it on at least 1.5× normal volume (compared with the same time of day over the last 10 sessions). It buys 1 share at the next candle with the stop just under the hammer and a 1:3 target, takes no entries after 2:30 PM, and squares off by 3:15 PM. Each evening the job replays the last 60 days of 5-minute candles (all Yahoo provides) and adds new trades to `intraday_trades.json`, so the record keeps growing. P&L is after Zerodha intraday charges and 0.02% slippage per side. The rule thresholds are at the top of `scan/intraday.py`.
  - The intraday log is filled in after the close, not live during market hours. Live alerts or automatic orders need a broker's live data feed, and real orders also need a SEBI-compliant broker API with a whitelisted static IP.
- **Changing the rules.** The thresholds sit at the top of `scan/scan.py`: `MAX_RISK` (10%), `MAX_EXT` (5% above ATH), `VOLX` (1.5× volume), `NEAR` (10% band for WAIT) and `STAR_ZONE`. If you change them, update the "How signals are decided" text in `site/index.html` to match.
- **If the daily job stops.** GitHub pauses scheduled jobs in a repository after 60 days with no commits and emails you when it does. Turn it back on from the **Actions** tab. A failed download stops the run before publishing, so the site keeps the previous day's data.
- **Run it on your own computer:** `pip install -r scan/requirements.txt`, then `python scan/scan.py`, then `python -m http.server -d site`, and open http://localhost:8000.

This is a rules-based screen for research, not investment advice.
