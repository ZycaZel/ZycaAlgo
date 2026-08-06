// Serves the structured trade log (data/trades.json), committed by the
// GitHub Actions daily job each time it enters, skips, or exits a position.
import fs from "fs";
import path from "path";

export default function handler(req, res) {
  try {
    const filePath = path.join(process.cwd(), "data", "trades.json");
    const raw = fs.readFileSync(filePath, "utf-8");
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "s-maxage=120, stale-while-revalidate=300");
    res.status(200).send(raw);
  } catch (err) {
    res.status(200).send("[]");
  }
}
