/**
 * ANane RAM license server (Cloudflare Worker + D1).
 *
 * Public API (used by the app):
 *   POST /api/activate  {key, hwid, version}  -> signed token, binds key to hwid on first use
 *   GET  /api/pubkey                          -> Ed25519 public key (raw, base64)
 *
 * Admin API (header X-Admin-Password):
 *   GET  /api/admin/keys
 *   POST /api/admin/keys      {type: "30d"|"lifetime", count, note}
 *   POST /api/admin/reset     {key}      clear HWID so it can move to another PC
 *   POST /api/admin/revoke    {key, revoked: true|false}
 *   POST /api/admin/delete    {key}
 *   GET  /admin               admin page
 *
 * Secrets: ADMIN_PASSWORD, SIGNING_KEY (PKCS8 base64), SIGNING_PUBLIC_KEY (raw base64)
 */

const DAY = 86400;
const THIRTY_DAYS = 30 * DAY;
const OFFLINE_GRACE = 24 * 3600;           // app may run this long without re-checking
const KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // 32 chars, no 0/O/1/I
const ADMIN_MAX_FAILURES = 10;
const ADMIN_FAILURE_WINDOW = 15 * 60;

const now = () => Math.floor(Date.now() / 1000);

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...extra,
    },
  });
}

const fail = (code, message, status = 400) => json({ ok: false, code, message }, status);

function b64(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s);
}

function unb64(text) {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function newKey() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  // 256 is a multiple of 32, so `% 32` is unbiased.
  const chars = Array.from(bytes, (b) => KEY_ALPHABET[b % 32]).join("");
  return `ANANE-${chars.slice(0, 4)}-${chars.slice(4, 8)}-${chars.slice(8, 12)}-${chars.slice(12, 16)}`;
}

function normalizeKey(value) {
  const key = String(value || "").trim().toUpperCase().replace(/\s+/g, "");
  return /^ANANE(-[A-Z0-9]{4}){4}$/.test(key) ? key : "";
}

let signingKeyPromise = null;
function signingKey(env) {
  if (!signingKeyPromise) {
    signingKeyPromise = crypto.subtle.importKey(
      "pkcs8", unb64(env.SIGNING_KEY), { name: "Ed25519" }, false, ["sign"],
    );
  }
  return signingKeyPromise;
}

async function signedToken(env, payload) {
  const text = JSON.stringify(payload);
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, await signingKey(env), new TextEncoder().encode(text));
  return { ok: true, payload: text, sig: b64(sig) };
}

async function readJson(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

/* ---------------------------------------------------------------- public */

async function activate(request, env) {
  const body = await readJson(request);
  if (!body) return fail("BAD_REQUEST", "Invalid request.");
  const key = normalizeKey(body.key);
  const hwid = String(body.hwid || "").toLowerCase();
  const version = String(body.version || "").slice(0, 32);
  if (!key) return fail("KEY_INVALID", "That doesn't look like a valid license key.");
  if (!/^[0-9a-f]{64}$/.test(hwid)) return fail("BAD_REQUEST", "Invalid device id.");

  const row = await env.DB.prepare("SELECT * FROM keys WHERE key = ?").bind(key).first();
  if (!row) return fail("KEY_NOT_FOUND", "This license key does not exist.", 404);
  if (row.revoked) return fail("KEY_REVOKED", "This license key has been disabled.", 403);

  const t = now();
  let { hwid: boundHwid, activated_at: activatedAt, expires_at: expiresAt } = row;

  if (!boundHwid) {
    // First use (or after an HWID reset): bind to this PC. A 30-day key starts
    // counting on its very first activation only; a reset does not restart it.
    activatedAt = activatedAt || t;
    if (row.type === "30d" && !expiresAt) expiresAt = activatedAt + THIRTY_DAYS;
    const res = await env.DB.prepare(
      "UPDATE keys SET hwid = ?, activated_at = ?, expires_at = ?, last_seen_at = ?, last_version = ? " +
      "WHERE key = ? AND hwid IS NULL",
    ).bind(hwid, activatedAt, expiresAt, t, version, key).run();
    if (!res.meta || res.meta.changes !== 1) {
      return fail("KEY_IN_USE", "This key was just activated on another device.", 409);
    }
    boundHwid = hwid;
  } else if (boundHwid !== hwid) {
    return fail("KEY_IN_USE", "This key is already used on another device.", 409);
  } else {
    await env.DB.prepare("UPDATE keys SET last_seen_at = ?, last_version = ? WHERE key = ?")
      .bind(t, version, key).run();
  }

  if (expiresAt && t >= expiresAt) return fail("KEY_EXPIRED", "This license key has expired.", 403);

  return json(await signedToken(env, {
    v: 1,
    key,
    hwid,
    type: row.type,
    expires_at: expiresAt || null,
    issued_at: t,
    offline_until: t + OFFLINE_GRACE,
  }));
}

/* ----------------------------------------------------------------- admin */

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

async function adminGuard(request, env) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const t = now();
  const rec = await env.DB.prepare("SELECT count, window_at FROM admin_failures WHERE ip = ?").bind(ip).first();
  if (rec && t - rec.window_at < ADMIN_FAILURE_WINDOW && rec.count >= ADMIN_MAX_FAILURES) {
    return fail("ADMIN_LOCKED", "Too many wrong passwords. Try again in 15 minutes.", 429);
  }
  const given = request.headers.get("x-admin-password") || "";
  const ok = env.ADMIN_PASSWORD && given &&
    crypto.subtle.timingSafeEqual(await sha256(given), await sha256(env.ADMIN_PASSWORD));
  if (ok) {
    if (rec) await env.DB.prepare("DELETE FROM admin_failures WHERE ip = ?").bind(ip).run();
    return null;
  }
  const fresh = !rec || t - rec.window_at >= ADMIN_FAILURE_WINDOW;
  await env.DB.prepare(
    "INSERT INTO admin_failures (ip, count, window_at) VALUES (?, 1, ?) " +
    "ON CONFLICT(ip) DO UPDATE SET count = ?, window_at = ?",
  ).bind(ip, t, fresh ? 1 : rec.count + 1, fresh ? t : rec.window_at).run();
  return fail("ADMIN_DENIED", "Wrong admin password.", 401);
}

async function adminListKeys(env) {
  const { results } = await env.DB.prepare(
    "SELECT key, type, note, created_at, hwid, activated_at, expires_at, last_seen_at, last_version, revoked " +
    "FROM keys ORDER BY created_at DESC, key",
  ).all();
  return json({ ok: true, now: now(), keys: results });
}

async function adminCreateKeys(request, env) {
  const body = (await readJson(request)) || {};
  const type = body.type === "lifetime" ? "lifetime" : body.type === "30d" ? "30d" : "";
  const count = Math.max(1, Math.min(100, parseInt(body.count, 10) || 1));
  const note = String(body.note || "").slice(0, 80);
  if (!type) return fail("BAD_REQUEST", "Choose 30 days or Lifetime.");
  const t = now();
  const created = [];
  const stmt = env.DB.prepare("INSERT INTO keys (key, type, note, created_at) VALUES (?, ?, ?, ?)");
  const batch = [];
  for (let i = 0; i < count; i++) {
    const key = newKey();
    created.push(key);
    batch.push(stmt.bind(key, type, note, t));
  }
  await env.DB.batch(batch);
  return json({ ok: true, keys: created });
}

async function adminKeyAction(request, env, action) {
  const body = (await readJson(request)) || {};
  const key = normalizeKey(body.key);
  if (!key) return fail("KEY_INVALID", "Invalid key.");
  let res;
  if (action === "reset") {
    res = await env.DB.prepare("UPDATE keys SET hwid = NULL WHERE key = ?").bind(key).run();
  } else if (action === "revoke") {
    res = await env.DB.prepare("UPDATE keys SET revoked = ? WHERE key = ?").bind(body.revoked === false ? 0 : 1, key).run();
  } else if (action === "delete") {
    res = await env.DB.prepare("DELETE FROM keys WHERE key = ?").bind(key).run();
  }
  if (!res || !res.meta || res.meta.changes !== 1) return fail("KEY_NOT_FOUND", "Key not found.", 404);
  return json({ ok: true });
}

/* ---------------------------------------------------------------- router */

import ADMIN_HTML from "./admin.html";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const method = request.method;

    try {
      if (path === "/api/activate" && method === "POST") return await activate(request, env);
      if (path === "/api/pubkey" && method === "GET") {
        return json({ ok: true, public_key: env.SIGNING_PUBLIC_KEY || "" });
      }
      if (path === "/api/health") return json({ ok: true, time: now() });

      if (path === "/admin" && method === "GET") {
        return new Response(ADMIN_HTML, {
          headers: {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "x-frame-options": "DENY",
            "referrer-policy": "no-referrer",
          },
        });
      }

      if (path.startsWith("/api/admin/")) {
        const denied = await adminGuard(request, env);
        if (denied) return denied;
        if (path === "/api/admin/keys" && method === "GET") return await adminListKeys(env);
        if (path === "/api/admin/keys" && method === "POST") return await adminCreateKeys(request, env);
        if (path === "/api/admin/reset" && method === "POST") return await adminKeyAction(request, env, "reset");
        if (path === "/api/admin/revoke" && method === "POST") return await adminKeyAction(request, env, "revoke");
        if (path === "/api/admin/delete" && method === "POST") return await adminKeyAction(request, env, "delete");
      }

      if (path === "/") return Response.redirect(`${url.origin}/admin`, 302);
      return fail("NOT_FOUND", "Not found.", 404);
    } catch (err) {
      console.error(err);
      return fail("SERVER_ERROR", "The license server had a problem. Try again shortly.", 500);
    }
  },
};
