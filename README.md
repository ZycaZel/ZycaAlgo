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
- **`scripts/discord_notify.py`** — optional: if `DISCORD_WEBHOOK_URL` is
  set, each day's qualifying signals are posted to a Discord channel via
  webhook, with cluster buys (2+ different insiders on the same ticker
  within 30 days) called out first. No-op if the env var isn't set.
- Both scheduled workflows (`daily-job.yml`, `backtest-refresh.yml`) post
  a failure alert to the same Discord webhook if any step fails, so a
  broken run doesn't just sit there unnoticed.
- **`account.html`** — real accounts (Supabase Auth), a per-user mode
  choice (manual vs AI-managed), a personal watchlist, and - if a trader
  connects their own Alpaca paper account - AI-managed mode. See
  "Optional: accounts and AI-managed trading" below.
- **`api/alpaca-connection.js`** — the only code path that ever touches a
  trader's Alpaca credentials: verifies their Supabase session, validates
  the keys against Alpaca before saving, encrypts them (AES-256-GCM), and
  stores them in a table with no client-readable RLS policies at all.
- **`scripts/ablation_study.py`** — splits the backtested signals one
  dimension at a time and Welch-tests whether the halves actually differ,
  so each filter has to earn its place in this data rather than resting on
  the paper that motivated it. Writes `data/backtest/ablation_report.md`.
  Runs monthly as part of the backtest refresh.
- **`scripts/technical_filter_study.py`** — asks whether a technical
  confirmation filter would improve the insider signal. It would not: see
  "What we tested and rejected" below. Not wired into any workflow, since
  it's a research result rather than part of the pipeline; run it by hand
  (`python technical_filter_study.py`) to reproduce.
- **`scripts/run_for_ai_managed_users.py`** — runs the same enter/manage
  logic as `trade_manager.py`, once per AI-managed trader with a connected
  account, each in their own state files under `data/users/<id>/` -
  completely separate from the site's own demo account. No-op if the
  Supabase/encryption secrets aren't set.

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

Optional additional secrets:
- `NOTION_TOKEN` — for the Notion watchlist sync (see below)
- `DISCORD_WEBHOOK_URL` — for Discord alerts on new signals

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

### 5. Optional: connect a custom domain
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

## What we tested and rejected

Adding a rule because it sounds sensible is how a strategy becomes a black
box. Two studies in `data/backtest/` record what actually survived testing.

**A technical confirmation filter — rejected.** The intuition is that an
insider buy is safer if the chart also looks healthy. Measured across 2,977
backtested signals (every indicator computed from bars strictly *before*
the filing date, so nothing leaks), it isn't:

| Condition | Result |
|---|---|
| Price above 200-day MA | Selects the *worse* half at short horizons |
| Price above 50-day MA | One significant horizon that flips sign out-of-sample |
| RSI(14) below 70 | No measurable effect |
| Volume above 20-day average | No measurable effect |
| Within 25% of 52-week high | Substantially worse, at every horizon |

The one robust effect runs opposite to the intuition: insider buys in
stocks near their 52-week high underperformed beaten-down names by 2-9
percentage points, significant both before and after the backtest's
in-sample cutoff. That is consistent with Lakonishok & Lee (2001) finding
insiders are contrarian buyers - trend confirmation fights the mechanism
the signal depends on.

It is deliberately **not** in the live pipeline. The account has run one
methodology since it opened, and changing the rules midway would turn its
public equity curve into a blend of two strategies that no longer
demonstrates either. Both sides of the out-of-sample split also sit inside
the same broadly rising market, so "beaten-down names recover" is a
plausible regime effect rather than a law. Testing it properly means a
second paper account running the variant in parallel, not editing this one.

## What's honest about this site

- Every "Get started" button now goes to `/account`, which is a real,
  working sign-up. No payment is collected anywhere and there is no paid
  tier — the invented $29/$79 pricing cards that used to sit on the home
  page were removed once accounts went live, because they advertised as
  paid a feature (mirroring signals into your own paper account) that is
  actually free and running.
- The dashboard shows a real, currently-running paper account. It is not a
  guarantee of future performance and not investment advice — see the
  footer disclaimer on the page itself.
