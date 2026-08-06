// Live account snapshot (equity, cash, buying power) from the PAPER endpoint.
// APCA_API_KEY_ID / APCA_API_SECRET_KEY are read from Vercel environment
// variables - never exposed to the browser, this function runs server-side.
export default async function handler(req, res) {
  try {
    const r = await fetch("https://paper-api.alpaca.markets/v2/account", {
      headers: {
        "APCA-API-KEY-ID": process.env.APCA_API_KEY_ID,
        "APCA-API-SECRET-KEY": process.env.APCA_API_SECRET_KEY,
      },
    });
    const data = await r.json();
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");
    res.status(r.status).json(data);
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
