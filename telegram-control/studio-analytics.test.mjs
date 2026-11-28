import {test} from "node:test";
import assert from "node:assert/strict";
import {AnalyticsHandler, DAILY_MS, RESERVATION_MS, STUDIO_CHANNELS, studioBridge} from "./studio-analytics.mjs";
import {openStudioState, sealStudioState} from "./studio-auth.mjs";
import {assertStudio, cloudSession, routeConfig, syncStudio} from "../scripts/zen_studio_sync.mjs";
import {miniApp} from "./miniapp.mjs";
import {HTML, CSS, JS} from "./ui.mjs";

const day = "2026-11-26";
const now = Date.parse(day + "T12:00:00Z");
const ids = Object.fromEntries(STUDIO_CHANNELS.map((channel, i) => [channel, "abc"[i].repeat(24)]));
const routes = Object.fromEntries(STUDIO_CHANNELS.map(channel => [channel,
 "https://dzen.ru/profile/editor/id/" + ids[channel] + "/publications-stat"]));
const key = "ab".repeat(32);
const mockSecret = "synthetic-cookie-secret";
// Ciphertext and session ids are random; escape them so a stray "+" or "." cannot break the pattern.
const escapeRe = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const raw = {cookies: [
 {domain: ".dzen.ru", name: "dzen", value: mockSecret, httpOnly: true, secure: true, expires: -1},
 {domain: "other.invalid", name: "other", value: "off-domain", httpOnly: true, secure: true, expires: -1},
], origins: [{origin: "https://dzen.ru", localStorage: []}, {origin: "https://other.invalid", localStorage: []}]};

class Storage {
 data = new Map();
 queue = Promise.resolve();
 async get(key) { return structuredClone(this.data.get(key)); }
 async transaction(update) {
  const result = this.queue.then(async () => {
   const data = structuredClone(this.data);
   const outcome = await update({get: async key => structuredClone(data.get(key)),
    put: async (key, value) => {
     if (key === this.failAt) throw Error("injected_write_failure");
     data.set(key, structuredClone(value));
    },
    delete: async key => data.delete(key)});
   this.data = data;
   return outcome;
  });
  this.queue = result.catch(() => {});
  return result;
 }
 history() { return [...this.data.keys()].filter(key => key.startsWith("history:")); }
}

function fixture() {
 const storage = new Storage();
 const sent = [];
 const requests = [];
 const env = {ZEN_STUDIO_SYNC_ENABLED: "true", ZEN_STUDIO_DISPATCH_TOKEN: "fixture-dispatch",
  TELEGRAM_OWNER_ID: "42", TELEGRAM_BOT_TOKEN: "fixture-telegram", ZEN_STUDIO_VAULT_KEY: key,
  DZEN_STUDIO_EXPORT_PATH: "/fixture/export", DZEN_STUDIO_EXPORT_LABEL: "Скачать XLSX"};
 for (const channel of STUDIO_CHANNELS) env["DZEN_" + channel.toUpperCase() + "_STATS_URL"] = routes[channel];
 const handler = new AnalyticsHandler({storage}, env, async (url, init) => {
  const body = JSON.parse(init.body);
  if (url.startsWith("https://api.telegram.org/")) {
   sent.push(body);
   return Response.json({ok: true});
  }
  requests.push({url, body});
  return new Response(null, {status: 204});
 }, () => now);
 const call = (verb, body) => handler.fetch(new Request("https://studio.internal/analytics/" + verb,
  body === undefined ? {} : {method: "POST", body: JSON.stringify(body)}));
 const body = async (verb, payload) => (await call(verb, payload)).json();
 return {env, handler, storage, sent, requests, call, body};
}

function report(channel, value = 3) {
 const metrics = {impressions: value, opens: 2, reads_and_views: 2, watch_minutes: 0.25};
 return {channel, channelId: ids[channel], digest: value.toString(16).padStart(64, "0"),
  start: "2026-11-20", end: day, dzen: metrics, all: {...metrics, comments: 0, subscriptions: 1, likes: 1}};
}

async function enroll(f) {
 const envelope = await sealStudioState(raw, key, routes.science);
 assert.deepEqual(await f.body("enroll", {envelope, generation: crypto.randomUUID()}), {ok: true});
 return envelope;
}

async function finish(f, mode, rows, extra = {}) {
 const run = crypto.randomUUID();
 const claim = await f.body("claim", {mode, run});
 assert.ok(claim.envelope, JSON.stringify(claim));
 assert.deepEqual(await f.body("reserve", {run, day, usedMs: 0}), {ok: true});
 return f.body("finish", {run, channelIds: ids, reports: rows, error: null, closed: true, elapsedMs: 2, ...extra});
}

test("AES-GCM seals only Dzen state and binds it to the approved original route", async () => {
 const first = await sealStudioState(raw, key, routes.science);
 const second = await sealStudioState(raw, key, routes.science);
 assert.notEqual(first.iv, second.iv);
 assert.doesNotMatch(JSON.stringify(first), /synthetic-cookie|off-domain/);
 assert.deepEqual(await openStudioState(first, key, routes.science), {cookies: [raw.cookies[0]], origins: [raw.origins[0]]});
 await assert.rejects(openStudioState(first, key, routes.cinema), /invalid_auth/);
 await assert.rejects(openStudioState(first, "bc".repeat(32), routes.science), /invalid_auth/);
 await assert.rejects(openStudioState({...first, ciphertext:
  (first.ciphertext[0] === "a" ? "b" : "a") + first.ciphertext.slice(1)}, key, routes.science), /invalid_auth/);
});

test("owner API and private runner API deny unauthorized requests before reading storage", async () => {
 const env = {ZEN_STUDIO_BRIDGE_SECRET: key, ZEN_STUDIO_ENROLL_SECRET: "bc".repeat(32),
  get REVIEW_JOURNAL() { throw Error("storage accessed"); }};
 const owner = new Request("https://example.invalid/api/analytics/refresh", {method: "POST", body: "{}"});
 assert.equal((await miniApp(owner, env)).status, 401);
 const runner = new Request("https://example.invalid/internal/zen-studio/enroll", {method: "POST",
  headers: {Authorization: "Bearer " + key}, body: "{}"});
 assert.equal((await studioBridge(runner, env)).status, 401);
});

test("three-channel acceptance gates hourly sync, stores history, and deduplicates replays", async () => {
 const f = fixture();
 const saved = await enroll(f);
 const denied = await f.body("claim", {mode: "all", run: crypto.randomUUID()});
 assert.equal(denied.error, "acceptance_required");
 assert.equal((await f.body("claim", {mode: "hourly", run: crypto.randomUUID()})).error, "acceptance_required");
 assert.equal((await f.body("claim", {mode: "all", run: crypto.randomUUID()})).error, "acceptance_required");
 assert.equal((await finish(f, "science", [report("science")])).ok, true);
 const full = STUDIO_CHANNELS.map(channel => report(channel));
 assert.equal((await finish(f, "all", full)).ok, true);
 assert.equal((await finish(f, "hourly", full)).ok, true);
 assert.equal(f.storage.history().length, 3);
 assert.equal((await finish(f, "hourly", STUDIO_CHANNELS.map(channel => report(channel, 6)))).ok, true);
 assert.equal(f.storage.history().length, 6);
 const status = await f.body("status");
 assert.equal(status.acceptedChannels, 3);
 assert.equal(status.channels[2].history.length, 2);
 assert.equal(status.channels[0].latest.dzen.impressions, 6);
 assert.ok(status.lastSuccess && status.manualAvailable);
 assert.doesNotMatch(JSON.stringify(status), new RegExp([mockSecret, saved.ciphertext.slice(0, 24), ids.science].map(escapeRe).join("|")));
 assert.equal((await f.call("refresh", {})).status, 202);
 assert.equal((await f.call("refresh", {})).status, 409);
 assert.deepEqual(f.requests[0].body, {ref: "main", inputs: {mode: "hourly"}});
});

test("bad channel binding and interrupted document writes preserve the last good result", async () => {
 const f = fixture();
 await enroll(f);
 assert.ok((await finish(f, "science", [report("science")])).ok);
 const full = STUDIO_CHANNELS.map(channel => report(channel));
 assert.ok((await finish(f, "all", full)).ok);
 const previous = await f.body("status");
 const mismatched = full.map(row => row.channel === "legends" ? {...row, channelId: ids.science} : row);
 assert.equal((await finish(f, "hourly", mismatched)).error, "workbook_invalid");
 assert.deepEqual((await f.body("status")).channels, previous.channels);
 const next = STUDIO_CHANNELS.map(channel => report(channel, 6));
 f.storage.failAt = "history:legends:" + next[1].digest;
 assert.equal((await finish(f, "hourly", next)).error, "studio_unavailable");
 assert.equal(f.storage.history().length, 3);
 assert.equal((await f.body("status")).lastSuccess, previous.lastSuccess);
 assert.deepEqual((await f.body("status")).channels, previous.channels);
});

test("budget exhaustion skips allocation, accounts for unconfirmed time, and keeps history", async () => {
 const f = fixture();
 await enroll(f);
 assert.ok((await finish(f, "science", [report("science")])).ok);
 const run = crypto.randomUUID();
 assert.ok((await f.body("claim", {mode: "science", run})).envelope);
 const exhausted = await f.body("reserve", {run, day, usedMs: DAILY_MS - RESERVATION_MS});
 assert.equal(exhausted.error, "quota_low");
 assert.equal((await f.body("finish", {run, error: "quota_low"})).error, "quota_low");
 assert.equal(f.storage.history().length, 1);
 assert.equal((await f.body("status")).quota.remainingMs, RESERVATION_MS - 2);
 assert.equal((await f.call("refresh", {})).status, 409);
});

test("expiry notifies once, challenge stops, and an uncertain close blocks the next lease", async () => {
 const f = fixture();
 await enroll(f);
 assert.equal((await finish(f, "science", [], {error: "auth_expired"})).error, "auth_expired");
 assert.equal((await f.body("status")).authorization, "expired");
 assert.equal((await f.body("claim", {mode: "science", run: crypto.randomUUID()})).error, "auth_missing");
 assert.equal(f.sent.length, 1);
 assert.equal(f.sent[0].chat_id, "42");
 await enroll(f);
 assert.equal((await finish(f, "science", [], {error: "challenge"})).error, "challenge");
 assert.equal(f.sent.length, 1);
 assert.equal((await f.body("status")).authorization, "challenge");
 await enroll(f);
 assert.equal((await finish(f, "science", [], {error: "session_failed", closed: false})).error, "closure_unconfirmed");
 assert.equal((await f.body("claim", {mode: "science", run: crypto.randomUUID()})).error, "closure_unconfirmed");
});

test("all cloud stages use the approved session and emit only safe diagnostics", async () => {
 const f = fixture();
 await enroll(f);
 const sid = crypto.randomUUID();
 const calls = [];
 let active = false;
 let clock = now;
 const client = {root: "https://fixture.invalid/devtools/browser",
  usage: async () => 35,
  active: async () => active ? [sid] : [],
  connect: async id => {
   assert.equal(id, sid);
   return {newContext: async options => {
    assert.equal(options.storageState.cookies[0].value, mockSecret);
    return {};
   }, close: async () => {}};
  },
  call: async (url, method) => {
   if (method === "POST") {
    assert.ok(url.endsWith("?keep_alive=10000"));
    active = true;
    calls.push("POST");
    return {sessionId: sid};
   }
   if (method === "DELETE") {
    active = false;
    calls.push("DELETE");
    return {status: "closing"};
   }
   throw Error("HTTP_404");
  }};
 const d = await syncStudio({mode: "science", env: f.env, request: f.body, client, now: () => ++clock,
  scan: async () => [report("science")]});
 assert.equal(d.status, "passed");
 assert.ok(d.closed);
 assert.deepEqual(calls, ["POST", "DELETE"]);
 assert.doesNotMatch(JSON.stringify(d), new RegExp([mockSecret, sid, ids.science].map(escapeRe).join("|")));
});

test("unknown allocation remains unconfirmed and never starts a second browser", async () => {
 const r = await cloudSession({client: {call: async () => { throw Error("HTTP_TIMEOUT"); }},
  scan: async () => {throw Error("unexpected");}});
 assert.equal(r.error, "closure_unconfirmed");
 assert.equal(r.allocated, false);
 assert.equal(r.attempted, true);
 assert.equal(r.closed, false);
});

test("route mismatch, login and CAPTCHA stop independently", async () => {
 const f = fixture();
 assert.deepEqual(Object.keys(routeConfig(f.env, "all").selected), STUDIO_CHANNELS);
 const location = (url, text) => ({url: () => url, locator: () => ({innerText: async () => text}),
  getByRole: () => ({or: () => ({filter: () => ({count: async () => 0})})})});
 const route = {url: routes.science};
 await assert.rejects(assertStudio(location(routes.cinema, "Статистика"), route), /channel_mismatch/);
 await assert.rejects(assertStudio(location("https://passport.yandex.ru/auth", "Вход"), route), /auth_expired/);
 await assert.rejects(assertStudio(location(routes.science, "подтвердите, что вы не робот"), route), /challenge/);
 await assertStudio(location(routes.science, "Статистика"), route);
 // Dzen adds view parameters after login; the same channel path still passes.
 await assertStudio(location(routes.science + "?statType=publications&publicationsSelectedField=impressions", "Статистика"), route);
 await assert.rejects(assertStudio(location(routes.cinema + "?statType=publications", "Статистика"), route), /channel_mismatch/);
 await assert.rejects(assertStudio(location(routes.science + "/other?statType=publications", "Статистика"), route), /channel_mismatch/);
});

test("statistics uses the existing themed app, owner endpoint and accessible controls", () => {
 assert.match(HTML, /data-tab="analytics"/);
 assert.match(HTML, /id="dzen-sync-status" role="status"/);
 assert.match(HTML, /id="dzen-sync-refresh" type="button" disabled/);
 assert.match(CSS, /\.analytics-metrics[^}]+var\(--txt\)/);
 assert.match(JS, /\/api\/analytics\/refresh/);
 assert.doesNotThrow(() => new Function(JS));
});
