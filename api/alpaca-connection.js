// Connect/check/disconnect a trader's own Alpaca PAPER account for
// AI-managed mode. This is the only code path in the entire site that ever
// touches a trader's Alpaca credentials.
//
// Security model:
//   - The caller's Supabase access token is verified against Supabase's own
//     auth server on every request - the user id always comes from that
//     verified token, never from anything the client claims in the request
//     body. A forged/expired token is simply rejected.
//   - Credentials are validated against Alpaca's real paper API before
//     being stored, so a typo doesn't silently get saved as "connected."
//   - Credentials are encrypted (AES-256-GCM) with a key that only exists
//     as a server-side env var, before ever reaching the database.
//   - Reads/writes to alpaca_connections use the Supabase SERVICE ROLE key
//     (server-side only) because that table intentionally has zero RLS
//     policies for the anon/authenticated roles - there is no client-side
//     path to this data at all, by design.
//   - The decrypted secret is never sent back to the browser in any
//     response, ever - only a connected/not-connected status.
import crypto from "crypto";

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
const ENCRYPTION_KEY = process.env.CREDENTIAL_ENCRYPTION_KEY;

function encrypt(plaintext) {
  const key = Buffer.from(ENCRYPTION_KEY, "base64");
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const encrypted = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, encrypted, tag]).toString("base64");
}

async function getVerifiedUser(req) {
  const auth = req.headers.authorization || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  if (!token) return null;
  const r = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { Authorization: `Bearer ${token}`, apikey: SUPABASE_ANON_KEY },
  });
  if (!r.ok) return null;
  const user = await r.json();
  return user && user.id ? user : null;
}

async function validateAlpacaCredentials(apiKeyId, apiSecretKey) {
  const r = await fetch("https://paper-api.alpaca.markets/v2/account", {
    headers: { "APCA-API-KEY-ID": apiKeyId, "APCA-API-SECRET-KEY": apiSecretKey },
  });
  return r.ok;
}

export default async function handler(req, res) {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY || !SUPABASE_SERVICE_ROLE_KEY || !ENCRYPTION_KEY) {
    res.status(500).json({ error: "Server not configured for Alpaca connections yet." });
    return;
  }

  const user = await getVerifiedUser(req);
  if (!user) {
    res.status(401).json({ error: "Not signed in." });
    return;
  }

  const serviceHeaders = {
    apikey: SUPABASE_SERVICE_ROLE_KEY,
    Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
    "Content-Type": "application/json",
  };

  try {
    if (req.method === "GET") {
      const r = await fetch(
        `${SUPABASE_URL}/rest/v1/alpaca_connections?user_id=eq.${user.id}&select=connected_at`,
        { headers: serviceHeaders }
      );
      const rows = await r.json();
      res.status(200).json({ connected: rows.length > 0, connected_at: rows[0] ? rows[0].connected_at : null });
      return;
    }

    if (req.method === "POST") {
      const { api_key_id, api_secret_key } = req.body || {};
      if (!api_key_id || !api_secret_key) {
        res.status(400).json({ error: "Both API key ID and secret key are required." });
        return;
      }
      const valid = await validateAlpacaCredentials(api_key_id, api_secret_key);
      if (!valid) {
        res.status(400).json({ error: "Alpaca rejected these credentials - double-check they're your PAPER trading keys." });
        return;
      }
      const payload = {
        user_id: user.id,
        encrypted_api_key: encrypt(api_key_id),
        encrypted_api_secret: encrypt(api_secret_key),
        connected_at: new Date().toISOString(),
      };
      const r = await fetch(`${SUPABASE_URL}/rest/v1/alpaca_connections?on_conflict=user_id`, {
        method: "POST",
        headers: { ...serviceHeaders, Prefer: "resolution=merge-duplicates" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        res.status(500).json({ error: "Could not save connection: " + (await r.text()) });
        return;
      }
      res.status(200).json({ connected: true });
      return;
    }

    if (req.method === "DELETE") {
      const r = await fetch(`${SUPABASE_URL}/rest/v1/alpaca_connections?user_id=eq.${user.id}`, {
        method: "DELETE",
        headers: serviceHeaders,
      });
      res.status(r.ok ? 200 : 500).json({ connected: false });
      return;
    }

    res.status(405).json({ error: "Method not allowed." });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
