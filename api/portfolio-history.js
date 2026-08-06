// Live equity curve from the PAPER endpoint's portfolio history.
export default async function handler(req, res) {
  try {
    const r = await fetch(
      "https://paper-api.alpaca.markets/v2/account/portfolio/history?period=1M&timeframe=1D",
      {
        headers: {
          "APCA-API-KEY-ID": process.env.APCA_API_KEY_ID,
          "APCA-API-SECRET-KEY": process.env.APCA_API_SECRET_KEY,
        },
      }
    );
    const data = await r.json();
    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
    res.status(r.status).json(data);
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
