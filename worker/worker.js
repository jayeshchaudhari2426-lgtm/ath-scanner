// Cloudflare Worker: receives TradingView alerts and serves the paper trades to the website.
//
//   POST /webhook     TradingView alert body (JSON from the Pine strategy). Needs the right "key".
//   GET  /api/trades  All dummy trades, newest data first, for the website.
//
// Bindings (Worker → Settings): D1 database as DB, secret WEBHOOK_SECRET.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...CORS },
  });

const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const isDate = (s) => typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s);

async function handleWebhook(req, env) {
  let b;
  try {
    b = JSON.parse(await req.text());
  } catch {
    return json({ error: "Body is not valid JSON. Use the alert message from the Pine strategy." }, 400);
  }
  if (!env.WEBHOOK_SECRET || b.key !== env.WEBHOOK_SECRET) {
    return json({ error: "Wrong key. The Pine input 'Webhook key' must equal WEBHOOK_SECRET." }, 403);
  }
  delete b.key;
  const sym = String(b.sym || "").toUpperCase().slice(0, 40);
  const date = isDate(b.date) ? b.date : new Date().toISOString().slice(0, 10);
  if (!sym) return json({ error: "Missing sym" }, 400);

  await env.DB.prepare("INSERT INTO events (body) VALUES (?)").bind(JSON.stringify(b)).run();

  if (b.event === "BUY") {
    const entry = num(b.entry), stop = num(b.stop), target = num(b.target);
    if (entry === null || stop === null || target === null || stop >= entry) {
      return json({ error: "BUY needs numeric entry, stop and target, with stop below entry" }, 400);
    }
    const open = await env.DB.prepare("SELECT id FROM trades WHERE sym = ? AND status = 'Open'").bind(sym).first();
    if (open) return json({ ok: true, note: "Already holding this stock; BUY ignored" });
    await env.DB.prepare(
      "INSERT OR IGNORE INTO trades (sym, exchange, name, conf, entry_date, entry, stop, target, qty, last_price, last_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)"
    ).bind(sym, String(b.exchange || ""), String(b.name || sym).slice(0, 120), String(b.conf || ""), date, entry, stop, target, entry, date).run();
    return json({ ok: true, event: "BUY", sym });
  }

  if (b.event === "EXIT") {
    const price = num(b.price);
    if (price === null) return json({ error: "EXIT needs a numeric price" }, 400);
    const status = ["Target hit", "Stop hit"].includes(b.reason) ? b.reason : "Closed";
    const r = await env.DB.prepare(
      "UPDATE trades SET status = ?, exit_price = ?, exit_date = ?, last_price = ?, last_date = ? WHERE sym = ? AND status = 'Open'"
    ).bind(status, price, date, price, date, sym).run();
    return json({ ok: true, event: "EXIT", sym, updated: r.meta ? r.meta.changes : undefined });
  }

  if (b.event === "MARK") {
    const price = num(b.price);
    if (price === null) return json({ error: "MARK needs a numeric price" }, 400);
    await env.DB.prepare("UPDATE trades SET last_price = ?, last_date = ? WHERE sym = ? AND status = 'Open'")
      .bind(price, date, sym).run();
    return json({ ok: true, event: "MARK", sym });
  }

  return json({ error: "Unknown event. Expected BUY, EXIT or MARK." }, 400);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    try {
      if (url.pathname === "/webhook" && req.method === "POST") return await handleWebhook(req, env);
      if (url.pathname === "/api/trades" && req.method === "GET") {
        const { results } = await env.DB.prepare("SELECT * FROM trades ORDER BY entry_date, id").all();
        return json({ updated: new Date().toISOString(), trades: results });
      }
      if (url.pathname === "/") return json({ ok: true, endpoints: ["POST /webhook", "GET /api/trades"] });
      return json({ error: "Not found" }, 404);
    } catch (e) {
      return json({ error: "Server error: " + e.message }, 500);
    }
  },
};
