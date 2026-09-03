// Proxies Yahoo Finance's chart endpoint for a single ticker's daily bars.
// Fetched server-side because Yahoo blocks unauthenticated cross-origin
// requests straight from a browser - this just forwards a browser-like
// User-Agent, which is all that endpoint actually checks for.
//
// Returns close, and also high/low, which the portfolio replay needs: a stop
// triggers on the day's low, not its close, and simulating one from closes
// alone would silently under-trigger every stop.
const RANGES = new Set(["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]);

export default async function handler(req, res) {
  const ticker = String(req.query.ticker || "").trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) {
    res.status(400).json({ error: "Invalid ticker." });
    return;
  }

  const range = RANGES.has(String(req.query.range)) ? String(req.query.range) : "3mo";

  try {
    const r = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=${range}&interval=1d`,
      { headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" } }
    );
    const data = await r.json();
    const result = data && data.chart && data.chart.result && data.chart.result[0];
    if (!result) {
      res.status(404).json({ error: `No price history found for ${ticker}.` });
      return;
    }

    const timestamps = result.timestamp || [];
    const q = result.indicators.quote[0] || {};
    const points = timestamps
      .map((t, i) => ({
        date: new Date(t * 1000).toISOString().slice(0, 10),
        close: (q.close || [])[i],
        high: (q.high || [])[i],
        low: (q.low || [])[i],
      }))
      .filter((p) => p.close != null);

    res.setHeader("Cache-Control", "s-maxage=3600, stale-while-revalidate=7200");
    res.status(200).json({ ticker, range, points });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
