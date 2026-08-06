// Serves the most recent scan result, committed to data/latest.json by the
// GitHub Actions daily job (and redeployed automatically by Vercel's GitHub
// integration whenever that commit lands).
import fs from "fs";
import path from "path";

export default function handler(req, res) {
  try {
    const filePath = path.join(process.cwd(), "data", "latest.json");
    const raw = fs.readFileSync(filePath, "utf-8");
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
    res.status(200).send(raw);
  } catch (err) {
    res.status(404).json({ error: "No scan data yet - the daily job hasn't run once successfully." });
  }
}
