# ZycaAlgo

SEC Form 4 insider-buy scanner, mirrored into an Alpaca **paper** trading
account, with a live dashboard on your own domain.

This account is always paper-only. `scripts/trade_manager.py` hard-codes
`https://paper-api.alpaca.markets` and never reads any other endpoint.

## How it fits together

- **`scripts/insider_buys.py`** — scans SEC EDGAR daily, applies the
  code-P / $100K / officer-director / non-10b5-1 / S&P500-or-NASDAQ filters.
- **`scripts/trade_manager.py`** — applies the $1B market cap + 500K volume
  liquidity filter, sizes positions at 2% of equity, places entries, and
  manages exits (15% stop -> trails 10% below the high once up 15% -> closes
  after 90 days flat).
- **`.github/workflows/daily-job.yml`** — runs both scripts on GitHub's
  servers Tuesday-Saturday at 15:00 Bangkok time (08:00 UTC) - shifted a day
  from the trading week because SEC's daily index for a trading day isn't
  ready until the following UTC morning - then commits
  the results back to this repo. Runs whether or not your computer is on.
- **`api/*.js`** — small serverless functions (deployed by Vercel) that read
  your live Alpaca account and today's scan results.
- **`index.html`** — the ZycaAlgo page itself, which calls those functions.
- **`scripts/notion_client.py`** / **`scripts/push_signals_to_notion.py`** —
  optional: if `NOTION_TOKEN` is set, each day's qualifying signals are also
  added to a personal Notion "Stock Watchlist" database (one row per ticker,
  a note per signal). No-op if the env var isn't set.
- **`scripts/build_watchlist.py`** — aggregates every archived daily scan
  into `data/watchlist.json`: one row per ticker ever flagged, enriched with
  sector/price from Yahoo Finance. Read directly by `watchlist.html` (no
  live API calls on page load).

## One-time setup

### 1. Get your Alpaca paper API keys
In your Alpaca dashboard: Paper Trading -> API Keys -> generate a key pair.
Keep both values handy for steps 3 and 4. Never put these in any file you
commit to the repo.

### 2. Create the GitHub repo
1. Go to github.com -> New repository -> name it (e.g. `zycaalgo`) -> Create.
2. From this folder, run:
   ```
   git init
   git add .
   git commit -m "Initial ZycaAlgo setup"
   git branch -M main
   git remote add origin https://github.com/<your-username>/zycaalgo.git
   git push -u origin main
   ```
3. In the repo on GitHub: **Settings -> Actions -> General -> Workflow
   permissions** -> select "Read and write permissions" -> Save. (The daily
   job needs this to commit its own results back.)

### 3. Add your Alpaca keys as GitHub secrets
Repo -> **Settings -> Secrets and variables -> Actions -> New repository
secret**. Add two:
- `APCA_API_KEY_ID`
- `APCA_API_SECRET_KEY`

Optional third secret, only if you want the Notion watchlist sync (see
below): `NOTION_TOKEN`.

You can test the workflow immediately without waiting for the schedule:
**Actions tab -> ZycaAlgo daily scan + paper trade -> Run workflow.**

### 4. Deploy the site on Vercel
1. vercel.com -> sign up (free) -> **Add New -> Project** -> import your
   `zycaalgo` GitHub repo.
2. Framework preset: "Other" (no build step needed).
3. **Settings -> Environment Variables**, add the same two keys:
   - `APCA_API_KEY_ID`
   - `APCA_API_SECRET_KEY`
4. Deploy. Vercel gives you a `zycaalgo-xxxx.vercel.app` URL immediately.
5. From then on, every push to `main` (including the daily bot commits)
   automatically redeploys.

### 5. Connect www.zycaalgo.com (or whatever you buy)
1. Buy the domain from any registrar (Namecheap, Squarespace Domains, etc.).
2. In Vercel: **Project -> Settings -> Domains -> Add** -> enter your domain.
3. Vercel shows you the DNS records to add (usually an A record or CNAME) —
   add those in your registrar's DNS settings.
4. DNS can take a few minutes to a few hours to propagate.

### 6. Optional: sync signals to a Notion watchlist
1. [notion.so/my-integrations](https://www.notion.so/my-integrations) ->
   **New integration** -> name it -> capabilities: Read/Update/Insert
   content (comments and user info not needed).
2. Copy the integration's access token.
3. In Notion, open the page containing your watchlist database -> **Share
   -> Connections** -> add the integration. It needs to be connected both
   to the parent page (so new research write-ups can be filed under it) and
   to the watchlist database itself (so rows can be added).
4. Your watchlist database needs at minimum a `Ticker` title property and a
   `Company Name` rich-text property; a `Status` status property with a
   `Researching` option is used for auto-added rows if present.
5. Add `NOTION_TOKEN` as a GitHub Actions secret (step 3 above) to enable
   the daily sync. Edit the two page/database IDs at the top of
   `scripts/notion_client.py` to point at your own workspace.

## Running locally (optional)

```
pip install -r scripts/requirements.txt
python scripts/insider_buys.py
APCA_API_KEY_ID=... APCA_API_SECRET_KEY=... python scripts/trade_manager.py manage
```

## What's honest about this site

- Every "Get started" button is a UI-only placeholder — no payment is
  collected anywhere.
- The dashboard shows a real, currently-running paper account. It is not a
  guarantee of future performance and not investment advice — see the
  footer disclaimer on the page itself.
