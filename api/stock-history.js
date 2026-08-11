// Proxies Yahoo Finance's chart endpoint for a single ticker's daily closes.
// Fetched server-side because Yahoo blocks unauthenticated cross-origin
// requests straight from a browser - this just forwards a browser-like
// User-Agent, which is all that endpoint actually checks for.
export default async function handler(req, res) {
  const ticker = String(req.query.ticker || "").trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) {
    res.status(400).json({ error: "Invalid ticker." });
    return;
  }

  try {
    const r = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=3mo&interval=1d`,
      { headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" } }
    );
    const data = await r.json();
    const result = data && data.chart && data.chart.result && data.chart.result[0];
    if (!result) {
      res.status(404).json({ error: `No price history found for ${ticker}.` });
      return;
    }

    const timestamps = result.timestamp || [];
    const closes = (result.indicators.quote[0] || {}).close || [];
    const points = timestamps
      .map((t, i) => ({ date: new Date(t * 1000).toISOString().slice(0, 10), close: closes[i] }))
      .filter((p) => p.close != null);

    res.setHeader("Cache-Control", "s-maxage=3600, stale-while-revalidate=7200");
    res.status(200).json({ ticker, points });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
