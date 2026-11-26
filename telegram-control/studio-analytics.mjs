import {validEnvelope} from "./studio-auth.mjs";

export const STUDIO_CHANNELS = ["science", "legends", "cinema"];
export const DAILY_MS = 480000;
export const RESERVATION_MS = 90000;
const LEASE_MS = 180000;
const METRICS = ["impressions", "opens", "reads_and_views", "watch_minutes", "comments", "subscriptions", "likes"];
const ERRORS = new Set(["auth_missing", "invalid_auth", "auth_expired", "challenge", "route_unconfigured",
 "channel_mismatch", "export_unverified", "download_failed", "workbook_invalid", "quota_unknown",
 "quota_low", "active_session", "session_failed", "closure_unconfirmed", "sync_failed", "auth_revoked"]);
const INIT = {auth: "missing", acceptance: 0, notification: null, latest: {}, lastSuccess: null};
const noStore = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"};
const reply = (body, status = 200) => Response.json(body, {status, headers: noStore});
const day = now => new Date(now).toISOString().slice(0, 10);
const timestamp = now => new Date(now).toISOString();
const exactDate = date => typeof date === "string" && /^\d{4}-\d\d-\d\d$/.test(date)
 && Number.isFinite(Date.parse(date)) && timestamp(Date.parse(date)).slice(0, 10) === date;
const id = value => typeof value === "string" && /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(value);
const approved = state => state.auth === "ready" && state.acceptance === 3 && !state.closeHold;

function project(state, now, enabled) {
 const quota = state.quota?.day === day(now) ? state.quota : null;
 const allocated = state.reservations?.day === day(now) ? state.reservations.ms : 0;
 const remainingMs = quota ? Math.max(0, DAILY_MS - quota.usedMs - allocated) : null;
 const busy = !!((state.lease && state.lease.expires > now) || state.requestUntil > now);
 const error = state.lease?.expires <= now ? "closure_unconfirmed" : state.error;
 const view = item => item ? {start: item.start, end: item.end, observedAt: item.observedAt,
  all: item.all, dzen: item.dzen} : null;
 return {
  authorization: state.auth, acceptedChannels: state.acceptance, lastSuccess: state.lastSuccess,
  lastAttempt: state.lastAttempt || null, error: error || null, notification: state.notification, busy,
  quota: {limitMs: DAILY_MS, remainingMs, checkedAt: quota ? timestamp(quota.at) : null},
  manualAvailable: Boolean(enabled && !busy && approved(state) && !error?.startsWith("closure_")
   && (remainingMs === null || remainingMs >= RESERVATION_MS)),
  channels: STUDIO_CHANNELS.map(channel => ({channel, latest: view(state.latest[channel]),
   history: (state.recent?.[channel] || []).map(view)})),
 };
}

function snapshot(raw, channel, expectedId, now) {
 if (raw?.channel !== channel || raw.channelId !== expectedId || !/^[a-f0-9]{24}$/.test(expectedId || "")
     || !/^[a-f0-9]{64}$/.test(raw.digest || "") || !exactDate(raw.start) || !exactDate(raw.end)
     || raw.start > raw.end || raw.end > day(now + 3 * 3600000)) throw Error("workbook_invalid");
 const clean = (group, keys) => Object.fromEntries(keys.map(key => {
  const value = group?.[key];
  if (!Number.isFinite(value) || value < 0 || (key !== "watch_minutes" && !Number.isSafeInteger(value)))
   throw Error("workbook_invalid");
  return [key, value];
 }));
 return {channel, channelId: expectedId, digest: raw.digest, start: raw.start, end: raw.end,
  observedAt: timestamp(now), all: clean(raw.all, METRICS), dzen: clean(raw.dzen, METRICS.slice(0, 4))};
}

export function analyticsStub(env) {
 const namespace = env.REVIEW_JOURNAL;
 return namespace?.get(namespace.idFromName("zen-studio-analytics-v1"));
}

async function sameSecret(expected, header) {
 if (!/^[a-f0-9]{64}$/i.test(expected || "") || !/^Bearer [a-f0-9]{64}$/i.test(header || "")) return false;
 const bytes = new TextEncoder();
 const key = await crypto.subtle.importKey("raw", bytes.encode(expected), {name: "HMAC", hash: "SHA-256"}, false, ["sign", "verify"]);
 const proof = await crypto.subtle.sign("HMAC", key, bytes.encode(expected));
 return crypto.subtle.verify("HMAC", key, proof, bytes.encode(header.slice(7)));
}

export async function studioBridge(request, env) {
 const path = new URL(request.url).pathname;
 if (!path.startsWith("/internal/zen-studio/")) return null;
 const action = path.split("/").at(-1);
 if (!["enroll", "revoke", "claim", "reserve", "finish"].includes(action) || path !== "/internal/zen-studio/" + action)
  return reply({error: "not_found"}, 404);
 if (request.method !== "POST") return reply({error: "method_not_allowed"}, 405);
 const secret = ["enroll", "revoke"].includes(action) ? env.ZEN_STUDIO_ENROLL_SECRET : env.ZEN_STUDIO_BRIDGE_SECRET;
 if (!await sameSecret(secret, request.headers.get("Authorization")))
  return reply({error: "unauthorized"}, 401);
 const stub = analyticsStub(env);
 if (!stub) return reply({error: "studio_unavailable"}, 503);
 return stub.fetch("https://studio.internal/analytics/" + action, {method: "POST", body: request.body});
}

export class AnalyticsHandler {
 constructor(ctx, env, fetcher = fetch, clock = Date.now) {
  this.storage = ctx.storage;
  this.env = env;
  this.fetcher = fetcher;
  this.clock = clock;
 }
 async fetch(request) {
  const path = new URL(request.url).pathname;
  const now = this.clock();
  const enabled = this.env.ZEN_STUDIO_SYNC_ENABLED === "true" && !!this.env.ZEN_STUDIO_DISPATCH_TOKEN;
  if (path === "/analytics/status" && request.method === "GET")
   return reply(project(await this.storage.get("status") || INIT, now, enabled));
  if (request.method !== "POST") return reply({error: "method_not_allowed"}, 405);
  if (path === "/analytics/refresh") return this.refresh(now, enabled);
  let data;
  try {
   const raw = await request.text();
   if (raw.length > 140000) return reply({error: "too_large"}, 413);
   data = JSON.parse(raw);
  } catch { return reply({error: "invalid_request"}, 400); }
  try {
   const result = await this.storage.transaction(async tx => {
    const state = await tx.get("status") || structuredClone(INIT);
    const write = async body => { await tx.put("status", state); return body; };
    switch (path) {
    case "/analytics/enroll": {
     if (!validEnvelope(data.envelope) || !id(data.generation)) return {status: 400, error: "invalid_auth"};
     if (state.lease || state.closeHold) return {status: 409, error: "session_failed"};
     if (await tx.get("generation:" + data.generation)) return {status: 409, error: "invalid_auth"};
     await tx.put("generation:" + data.generation, true);
     await tx.put("vault", {generation: data.generation, envelope: {version: 1,
      iv: data.envelope.iv, ciphertext: data.envelope.ciphertext}});
     Object.assign(state, {auth: "ready", acceptance: 0, error: null, notification: null});
     return write({ok: true});
    }
    case "/analytics/revoke":
     await tx.delete("vault");
     Object.assign(state, {auth: "missing", acceptance: 0, error: "auth_revoked", notification: null});
     return write({ok: true});
    case "/analytics/claim": {
     const mode = data.mode;
     if (!["science", "all", "hourly"].includes(mode) || !id(data.run)) return {status: 400, error: "invalid_request"};
     if (state.lease?.expires <= now && !state.lease.reserved) delete state.lease;
     if (state.lease || state.closeHold) return {status: 409, error: "closure_unconfirmed"};
     if (await tx.get("run:" + data.run)) return {status: 409, error: "duplicate_run"};
     const vault = await tx.get("vault");
     if (!vault || state.auth !== "ready") return {status: 409, error: "auth_missing"};
     if ((mode === "all" && state.acceptance < 1) || (mode === "hourly" &&
       (state.acceptance !== 3 || this.env.ZEN_STUDIO_SYNC_ENABLED !== "true")))
      return {status: 409, error: "acceptance_required"};
     await tx.put("run:" + data.run, true);
     state.lease = {run: data.run, generation: vault.generation, mode, expires: now + LEASE_MS};
     state.requestUntil = null;
     state.lastAttempt = timestamp(now);
     return write({envelope: vault.envelope});
    }
    case "/analytics/reserve": {
     if (!id(data.run) || state.lease?.run !== data.run || state.lease.expires <= now || state.lease.reserved)
      return {status: 409, error: "invalid_lease"};
     if (!Number.isSafeInteger(data.usedMs) || data.usedMs < 0 || data.day !== day(now))
      return {status: 400, error: "quota_unknown"};
     const vault = await tx.get("vault");
     if (state.auth !== "ready" || vault?.generation !== state.lease.generation)
      return {status: 409, error: "auth_revoked"};
     const spent = state.reservations?.day === data.day ? state.reservations.ms : 0;
     const usedMs = Math.max(data.usedMs, state.quota?.day === data.day ? state.quota.usedMs : 0);
     state.quota = {day: data.day, at: now, usedMs};
     // Keep every reservation for the UTC day. Analytics can lag behind a closed session.
     if (usedMs + spent + RESERVATION_MS > DAILY_MS || day(now + RESERVATION_MS) !== data.day)
      return write({status: 429, error: "quota_low"});
     state.reservations = {day: data.day, ms: spent + RESERVATION_MS};
     state.lease.reserved = true;
     return write({ok: true});
    }
    case "/analytics/finish": {
     const lease = state.lease;
     if (!id(data.run) || lease?.run !== data.run) return {status: 409, error: "invalid_lease"};
     const vault = await tx.get("vault");
     const current = vault?.generation === lease.generation;
     const channelIds = data.channelIds || {};
     const expected = lease.mode === "science" ? ["science"] : STUDIO_CHANNELS;
     let error = data.error === null ? null : ERRORS.has(data.error) ? data.error : "sync_failed";
     if (!current) error = "auth_revoked";
     delete state.lease;
     if (lease.reserved && data.closed !== true) {
      state.closeHold = true;
      return write({ok: false, error: state.error = "closure_unconfirmed"});
     }
     if (lease.reserved && state.reservations?.day === day(now) && Number.isSafeInteger(data.elapsedMs)
         && data.elapsedMs > 0 && data.elapsedMs <= 60000) {
      state.reservations.ms -= RESERVATION_MS - data.elapsedMs;
     }
     const rows = Array.isArray(data.reports) ? data.reports : [];
     let clean;
     if (!error) {
      try {
       if (!lease.reserved || rows.length !== expected.length ||
           new Set(rows.map(row => row?.channel)).size !== expected.length ||
           new Set(expected.map(channel => channelIds[channel])).size !== expected.length) throw Error();
       clean = expected.map(channel => snapshot(rows.find(row => row?.channel === channel),
        channel, channelIds[channel], now));
       // Check every binding before storing any channel in the collection.
       for (const item of clean) {
        if (state.latest[item.channel]?.channelId && state.latest[item.channel].channelId !== item.channelId
            || Object.values(state.latest).some(old => old.channel !== item.channel && old.channelId === item.channelId))
         throw Error();
       }
      } catch { error = "workbook_invalid"; }
     }
     if (!error) {
      for (const item of clean) {
       const key = "history:" + item.channel + ":" + item.digest;
       const previous = await tx.get(key);
       if (!previous) {
        await tx.put(key, item);
        state.recent ||= {};
        state.recent[item.channel] = [item, ...(state.recent[item.channel] || [])].slice(0, 12);
       }
       if (!state.latest[item.channel] || !previous && item.end >= state.latest[item.channel].end)
        state.latest[item.channel] = previous || item;
      }
      if (expected.length === 3) state.lastSuccess = timestamp(now);
      state.acceptance = Math.max(state.acceptance, expected.length);
     }
     state.error = error;
     let notify;
     if (current && ["auth_expired", "challenge", "invalid_auth"].includes(error)) {
      state.auth = error === "auth_expired" ? "expired" : error === "challenge" ? "challenge" : "invalid";
      if (error === "auth_expired" && !state.notification) {
       state.notification = "sending";
       notify = vault.generation;
      }
     }
     return write({ok: !error, error, notify});
    }
    default: return {status: 404, error: "not_found"};
    }
   });
   if (result.notify) {
    await this.notify(result.notify);
    delete result.notify;
   }
   const {status = 200, ...body} = result;
   return reply(body, status);
  } catch { return reply({error: "studio_unavailable"}, 503); }
 }
 async refresh(now, enabled) {
  const claimed = await this.storage.transaction(async tx => {
   const state = await tx.get("status") || structuredClone(INIT);
   if (!project(state, now, enabled).manualAvailable) return false;
   state.requestUntil = now + LEASE_MS;
   await tx.put("status", state);
   return true;
  });
  if (!claimed) return reply({error: "refresh_unavailable"}, 409);
  try {
   const response = await this.fetcher("https://api.github.com/repos/kaokshi/kaokshi.github.io/actions/workflows/zen-studio-sync.yml/dispatches",
    {method: "POST", headers: {Authorization: "Bearer " + this.env.ZEN_STUDIO_DISPATCH_TOKEN,
      "User-Agent": "ZenControl", "Content-Type": "application/json", Accept: "application/vnd.github+json"},
     body: JSON.stringify({ref: "main", inputs: {mode: "hourly"}}), redirect: "error",
     signal: AbortSignal.timeout(10000)});
   if (response.ok) return reply({queued: true}, 202);
  } catch {}
  return reply({error: "dispatch_unconfirmed"}, 503);
 }
 async notify(generation) {
  let sent = false;
  if (this.env.TELEGRAM_BOT_TOKEN && /^[1-9]\d{0,15}$/.test(this.env.TELEGRAM_OWNER_ID || "")) {
   try {
    const response = await this.fetcher("https://api.telegram.org/bot" + this.env.TELEGRAM_BOT_TOKEN + "/sendMessage",
     {method: "POST", headers: {"Content-Type": "application/json"}, redirect: "error",
      signal: AbortSignal.timeout(10000), body: JSON.stringify({chat_id: this.env.TELEGRAM_OWNER_ID,
       text: "ZEN CONTROL · Дзен запросил повторный вход. Войдите через официальный интерфейс Дзена, чтобы возобновить сбор статистики."})});
    sent = response.ok && (await response.json()).ok === true;
   } catch {}
  }
  await this.storage.transaction(async tx => {
   const current = await tx.get("vault");
   if (current?.generation !== generation) return;
   const state = await tx.get("status");
   state.notification = sent ? "sent" : "unconfirmed";
   await tx.put("status", state);
  });
 }
}
