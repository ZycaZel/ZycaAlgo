// Given a ticker, returns: the issuer's genuine insider-buy history (code P,
// officer/director, not a 10b5-1 plan - same definition used by the daily
// scanner) plus recent daily price bars, for the chart-overlay explorer page.
//
// Ports the parsing logic from scripts/insider_buys.py to JS since Vercel
// functions can't easily share code with the Python GitHub Actions job.

const SEC_HEADERS = {
  "User-Agent": "ZycaAlgo nattawutgorn@gmail.com",
  "Accept-Encoding": "gzip, deflate",
};

// Yahoo Finance's unofficial endpoints expect a normal-looking browser
// request, not an identifying bot User-Agent like SEC requires.
const YAHOO_HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
};

async function fetchText(url) {
  const r = await fetch(url, { headers: SEC_HEADERS });
  if (!r.ok) return null;
  return await r.text();
}

async function fetchYahooJson(url) {
  const r = await fetch(url, { headers: YAHOO_HEADERS });
  if (!r.ok) return null;
  try {
    return await r.json();
  } catch {
    return null;
  }
}

function extract(re, text) {
  const m = re.exec(text);
  return m ? m[1].trim() : null;
}

function isTrue(v) {
  return v != null && ["1", "true"].includes(String(v).trim().toLowerCase());
}

async function resolveTicker(ticker) {
  const text = await fetchText("https://www.sec.gov/files/company_tickers.json");
  if (!text) return null;
  const data = JSON.parse(text);
  const upper = ticker.toUpperCase();
  for (const key in data) {
    if (data[key].ticker === upper) {
      return { cik: String(data[key].cik_str), name: data[key].title };
    }
  }
  return null;
}

async function getFilingList(cik, sinceDate) {
  const url = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${cik}&type=4&dateb=&owner=include&count=100&output=atom`;
  const text = await fetchText(url);
  if (!text) return [];
  const entries = text.match(/<entry>[\s\S]*?<\/entry>/g) || [];
  const out = [];
  for (const entry of entries) {
    const date = extract(/<filing-date>([^<]+)<\/filing-date>/, entry);
    const href = extract(/<filing-href>([^<]+)<\/filing-href>/, entry);
    if (!date || !href) continue;
    if (sinceDate && date < sinceDate) continue;
    out.push({ date, href });
  }
  return out;
}

async function findXmlUrl(indexUrl) {
  const html = await fetchText(indexUrl);
  if (!html) return null;
  const candidates = [...html.matchAll(/href="([^"]+\.xml)"/gi)]
    .map((m) => m[1])
    .filter((c) => !/\/xsl[^/]*\//i.test(c) && !/(_cal|_def|_lab|_pre)\.xml$/i.test(c));
  if (!candidates.length) return null;
  return new URL(candidates[0], indexUrl).href;
}

function parseForm4(xml) {
  const isDirector = isTrue(extract(/<isDirector>([^<]*)<\/isDirector>/, xml));
  const isOfficer = isTrue(extract(/<isOfficer>([^<]*)<\/isOfficer>/, xml));
  const officerTitle = isOfficer ? extract(/<officerTitle>([^<]*)<\/officerTitle>/, xml) : null;
  const ownerName = extract(/<rptOwnerName>([^<]*)<\/rptOwnerName>/, xml);

  const titleParts = [];
  if (officerTitle) titleParts.push(officerTitle);
  else if (isOfficer) titleParts.push("Officer");
  if (isDirector) titleParts.push("Director");
  const title = titleParts.length ? titleParts.join(", ") : "Insider";

  const docLevelPlanRaw = extract(/<aff10b5One>\s*(?:<value>)?\s*([^<]*)/, xml);

  const txBlocks = xml.match(/<nonDerivativeTransaction>[\s\S]*?<\/nonDerivativeTransaction>/g) || [];
  const transactions = txBlocks.map((block) => {
    const code = extract(/<transactionCode>([^<]*)<\/transactionCode>/, block);
    const shares = extract(/<transactionShares>[\s\S]*?<value>([^<]*)<\/value>/, block);
    const price = extract(/<transactionPricePerShare>[\s\S]*?<value>([^<]*)<\/value>/, block);
    const date = extract(/<transactionDate>[\s\S]*?<value>([^<]*)<\/value>/, block);
    const txPlanRaw = extract(/<aff10b5One>\s*(?:<value>)?\s*([^<]*)/, block) ?? docLevelPlanRaw;
    return { code, shares, price, date, is10b5: isTrue(txPlanRaw) };
  });

  return { isDirector, isOfficer, title, ownerName, transactions };
}

async function processFiling(filing) {
  const xmlUrl = await findXmlUrl(filing.href);
  if (!xmlUrl) return [];
  const xml = await fetchText(xmlUrl);
  if (!xml) return [];
  let parsed;
  try {
    parsed = parseForm4(xml);
  } catch {
    return [];
  }
  if (!(parsed.isOfficer || parsed.isDirector)) return [];

  const results = [];
  for (const tx of parsed.transactions) {
    if (tx.code !== "P" || tx.is10b5) continue;
    const shares = parseFloat(tx.shares);
    const price = parseFloat(tx.price);
    if (!isFinite(shares) || !isFinite(price)) continue;
    results.push({
      date: tx.date,
      insider: parsed.ownerName || "Unknown",
      title: parsed.title,
      shares,
      price,
      total: shares * price,
      filing_url: filing.href,
    });
  }
  return results;
}

async function mapWithConcurrency(items, limit, fn) {
  const results = [];
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      results[idx] = await fn(items[idx]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

async function getPriceBars(ticker, start) {
  const period1 = Math.floor(new Date(start).getTime() / 1000);
  const period2 = Math.floor(Date.now() / 1000);
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}`
    + `?period1=${period1}&period2=${period2}&interval=1d`;
  const data = await fetchYahooJson(url);
  const result = data && data.chart && data.chart.result && data.chart.result[0];
  if (!result || !result.timestamp) return [];
  const closes = result.indicators.quote[0].close;
  const volumes = result.indicators.quote[0].volume;
  return result.timestamp
    .map((t, i) => ({ t: new Date(t * 1000).toISOString(), c: closes[i], v: volumes[i] }))
    .filter((b) => b.c != null);
}

async function getNews(ticker) {
  const url = `https://query1.finance.yahoo.com/v1/finance/search`
    + `?q=${encodeURIComponent(ticker)}&newsCount=8&quotesCount=0`;
  const data = await fetchYahooJson(url);
  const items = (data && data.news) || [];
  return items.map((n) => ({
    title: n.title,
    publisher: n.publisher,
    link: n.link,
    published: n.providerPublishTime ? new Date(n.providerPublishTime * 1000).toISOString() : null,
  }));
}

export default async function handler(req, res) {
  const ticker = (req.query.ticker || "").trim().toUpperCase();
  if (!ticker || !/^[A-Z.]{1,10}$/.test(ticker)) {
    res.status(400).json({ error: "Provide a valid ?ticker= (letters only)." });
    return;
  }

  try {
    const monthsBack = Math.min(parseInt(req.query.months || "12", 10) || 12, 24);
    const since = new Date();
    since.setMonth(since.getMonth() - monthsBack);
    const sinceStr = since.toISOString().slice(0, 10);

    const company = await resolveTicker(ticker);
    if (!company) {
      res.status(404).json({ error: `No SEC-registered company found for ticker ${ticker}.` });
      return;
    }

    const filings = (await getFilingList(company.cik, sinceStr)).slice(0, 40);
    const perFilingResults = await mapWithConcurrency(filings, 5, processFiling);
    const buys = perFilingResults.flat().sort((a, b) => (a.date < b.date ? -1 : 1));

    const [bars, news] = await Promise.all([
      getPriceBars(ticker, sinceStr),
      getNews(ticker),
    ]);

    res.setHeader("Cache-Control", "s-maxage=600, stale-while-revalidate=1800");
    res.status(200).json({
      ticker,
      company: company.name,
      cik: company.cik,
      since: sinceStr,
      filings_checked: filings.length,
      buys,
      bars: bars.map((b) => ({ t: b.t, c: b.c, v: b.v })),
      news,
    });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
