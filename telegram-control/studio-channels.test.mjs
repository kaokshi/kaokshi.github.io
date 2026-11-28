// Channel registry and per-channel (per-account) Studio sessions. Synthetic fixtures only.
import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {AnalyticsHandler} from "./studio-analytics.mjs";
import {sealStudioState} from "./studio-auth.mjs";
import {PRIMARY_CHANNEL, STUDIO_CHANNELS, STUDIO_CHANNEL_REGISTRY, vaultKey} from "./studio-channels.mjs";
import {channelRoutes, routeConfig, syncStudio} from "../scripts/zen_studio_sync.mjs";

const day = "2026-11-26";
const now = Date.parse(day + "T12:00:00Z");
const ids = Object.fromEntries(STUDIO_CHANNELS.map((channel, i) => [channel, "abcdef"[i].repeat(24)]));
const routes = Object.fromEntries(STUDIO_CHANNELS.map(channel => [channel,
 "https://dzen.ru/profile/editor/id/" + ids[channel] + "/publications-stat"]));
const key = "ab".repeat(32);
const state = value => ({cookies: [{domain: ".dzen.ru", name: "dzen", value, httpOnly: true, secure: true, expires: -1}],
 origins: [{origin: "https://dzen.ru", localStorage: []}]});

class Storage {
 data = new Map();
 async get(k) { return structuredClone(this.data.get(k)); }
 async transaction(update) {
  const data = structuredClone(this.data);
  const out = await update({get: async k => structuredClone(data.get(k)),
   put: async (k, v) => { data.set(k, structuredClone(v)); }, delete: async k => data.delete(k)});
  this.data = data;
  return out;
 }
}

function fixture() {
 const storage = new Storage();
 const env = {ZEN_STUDIO_SYNC_ENABLED: "true", ZEN_STUDIO_VAULT_KEY: key,
  DZEN_STUDIO_EXPORT_PATH: "/fixture/export", DZEN_STUDIO_EXPORT_LABEL: "Отчёт",
  DZEN_STUDIO_ROUTES: JSON.stringify(routes)};
 const handler = new AnalyticsHandler({storage}, env, async () => Response.json({ok: true}), () => now);
 const body = async (verb, payload) => (await handler.fetch(new Request("https://studio.internal/analytics/" + verb,
  payload === undefined ? {} : {method: "POST", body: JSON.stringify(payload)}))).json();
 return {storage, env, body};
}
const enroll = async (f, channel, secret) => {
 const envelope = await sealStudioState(state(secret), key, routes[channel]);
 const payload = {envelope, generation: crypto.randomUUID(), ...(channel === PRIMARY_CHANNEL ? {} : {channel})};
 assert.deepEqual(await f.body("enroll", payload), {ok: true});
};
const report = (channel, value = 3) => {
 const metrics = {impressions: value, opens: 2, reads_and_views: 2, watch_minutes: 0.25};
 return {channel, channelId: ids[channel], digest: value.toString(16).padStart(64, "0"), start: "2026-11-20",
  end: day, dzen: metrics, all: {...metrics, comments: 0, subscriptions: 1, likes: 1}};
};
async function finish(f, mode, rows) {
 const run = crypto.randomUUID();
 const claim = await f.body("claim", {mode, run});
 assert.ok(claim.envelope, JSON.stringify(claim));
 assert.deepEqual(await f.body("reserve", {run, day, usedMs: 0}), {ok: true});
 return f.body("finish", {run, channelIds: ids, reports: rows, error: null, closed: true, elapsedMs: 2});
}

test("the JS registry and the Python importer registry are identical", () => {
 const json = JSON.parse(readFileSync(new URL("../config/zen_studio_channels.json", import.meta.url), "utf8"));
 assert.deepEqual(json.channels, STUDIO_CHANNEL_REGISTRY.map(row => ({...row})));
 assert.equal(PRIMARY_CHANNEL, "science");
 assert.equal(vaultKey(PRIMARY_CHANNEL), "vault", "the primary keeps the original storage key");
 assert.equal(vaultKey("cinema"), "vault:cinema");
});

test("each channel can use its own account session; others fall back to the primary account", async () => {
 const f = fixture();
 await enroll(f, "science", "science-account");
 assert.equal((await finish(f, "science", [report("science")])).ok, true);
 await enroll(f, "cinema", "cinema-account");
 assert.ok(f.storage.data.has("vault:cinema"));
 const status = await f.body("status");
 assert.equal(status.acceptedChannels, 1, "a new channel login keeps the primary acceptance only");
 const run = crypto.randomUUID();
 const claim = await f.body("claim", {mode: "all", run});
 assert.deepEqual(Object.keys(claim.envelopes).sort(), [...STUDIO_CHANNELS].sort());
 assert.equal(claim.envelopes.cinema.bound, "cinema");
 assert.equal(claim.envelopes.legends.bound, PRIMARY_CHANNEL, "no own session yet: primary account");
 assert.equal(claim.envelopes.science.bound, PRIMARY_CHANNEL);
 assert.notEqual(claim.envelopes.cinema.envelope.ciphertext, claim.envelopes.science.envelope.ciphertext);
 assert.deepEqual(await f.body("finish", {run, error: "export_unverified", closed: true, reports: []}),
  {ok: false, error: "export_unverified"});
});

test("single-channel acceptance builds up to the full registry before hourly sync", async () => {
 const f = fixture();
 await enroll(f, "science", "science-account");
 assert.equal((await f.body("claim", {mode: "cinema", run: crypto.randomUUID()})).error, "acceptance_required");
 assert.equal((await finish(f, "science", [report("science")])).ok, true);
 await enroll(f, "cinema", "cinema-account");
 await enroll(f, "legends", "legends-account");
 assert.equal((await finish(f, "cinema", [report("cinema")])).ok, true);
 assert.equal((await f.body("claim", {mode: "hourly", run: crypto.randomUUID()})).error, "acceptance_required");
 assert.equal((await finish(f, "legends", [report("legends")])).ok, true);
 assert.equal((await f.body("status")).acceptedChannels, STUDIO_CHANNELS.length);
 assert.equal((await finish(f, "hourly", STUDIO_CHANNELS.map(channel => report(channel, 6)))).ok, true);
 assert.ok((await f.body("status")).lastSuccess);
});

test("a channel re-login during a run revokes that run; revoking one channel keeps the others", async () => {
 const f = fixture();
 await enroll(f, "science", "science-account");
 assert.equal((await finish(f, "science", [report("science")])).ok, true);
 await enroll(f, "cinema", "cinema-account");
 const run = crypto.randomUUID();
 assert.ok((await f.body("claim", {mode: "cinema", run})).envelopes.cinema);
 assert.deepEqual(await f.body("reserve", {run, day, usedMs: 0}), {ok: true});
 const swap = f.storage.data.get("vault:cinema");
 f.storage.data.set("vault:cinema", {...swap, generation: crypto.randomUUID()});
 const done = await f.body("finish", {run, channelIds: ids, reports: [report("cinema")], error: null, closed: true, elapsedMs: 2});
 assert.equal(done.error, "auth_revoked");
 assert.deepEqual(await f.body("revoke", {channel: "cinema"}), {ok: true});
 assert.equal(f.storage.data.has("vault:cinema"), false);
 assert.ok(f.storage.data.has("vault"), "the primary account session stays");
 assert.equal((await f.body("status")).authorization, "ready");
 assert.equal((await f.body("revoke", {channel: "unknown"})).error, "invalid_request");
});

test("routes come from one JSON secret, legacy per-channel values still work", () => {
 assert.deepEqual(channelRoutes({DZEN_STUDIO_ROUTES: JSON.stringify(routes)}), routes);
 const legacy = Object.fromEntries(STUDIO_CHANNELS.map(c => ["DZEN_" + c.toUpperCase() + "_STATS_URL", routes[c]]));
 assert.deepEqual(channelRoutes(legacy), routes);
 const env = {DZEN_STUDIO_ROUTES: JSON.stringify(routes), DZEN_STUDIO_EXPORT_PATH: "/fixture/export", DZEN_STUDIO_EXPORT_LABEL: "Отчёт"};
 assert.deepEqual(Object.keys(routeConfig(env, "cinema").selected), ["cinema"]);
 assert.deepEqual(Object.keys(routeConfig(env, "all").selected), STUDIO_CHANNELS);
 assert.throws(() => channelRoutes({DZEN_STUDIO_ROUTES: "not json"}), /route_unconfigured/);
});

test("collection opens one isolated browser context per account session", async () => {
 const f = fixture();
 await enroll(f, "science", "science-account");
 assert.equal((await finish(f, "science", [report("science")])).ok, true);
 await enroll(f, "cinema", "cinema-account");
 const sid = crypto.randomUUID();
 let active = false, clock = now;
 const contexts = [];
 const client = {root: "https://fixture.invalid/devtools/browser", usage: async () => 0,
  active: async () => active ? [sid] : [],
  connect: async () => ({newContext: async options => { const c = {cookie: options.storageState.cookies[0].value}; contexts.push(c); return c; },
   close: async () => {}}),
  call: async (url, method) => {
   if (method === "POST") { active = true; return {sessionId: sid}; }
   if (method === "DELETE") { active = false; return {status: "closing"}; }
   throw Error("HTTP_404");
  }};
 let seen;
 const scan = async (context, selected, step, contextFor) => {
  seen = Object.fromEntries(Object.entries(contextFor).map(([channel, c]) => [channel, c.cookie]));
  return STUDIO_CHANNELS.map(channel => report(channel));
 };
 const result = await syncStudio({mode: "all", env: f.env, request: f.body, client, now: () => ++clock, scan});
 assert.equal(result.status, "passed", JSON.stringify(result));
 assert.equal(contexts.length, 2, "science and legends share the primary account, cinema has its own");
 assert.deepEqual(seen, {science: "science-account", legends: "science-account", cinema: "cinema-account"});
});
