import {createRequire} from "node:module";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {pathToFileURL} from "node:url";
import {DAILY_MS, RESERVATION_MS, STUDIO_CHANNELS} from "../telegram-control/studio-analytics.mjs";
import {PRIMARY_CHANNEL, validChannel} from "../telegram-control/studio-channels.mjs";
import {openStudioState, studioRoute} from "../telegram-control/studio-auth.mjs";
const require = createRequire(import.meta.url);
const {analytics} = require("./zen_browser_usage_readonly.cjs");
const {confirmClose} = require("./zen_browser_close.cjs");
const ORIGIN = "https://dzen.ru";
const MAX_XLSX = 10_000_000;
const CAP = 60000;
const KEEP_ALIVE = 10000;
const WORK = 33000;
const STATS_VIEW = "?statType=publications";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const UTC = now => new Date(now).toISOString().slice(0, 10);
const SAFE = new Set(["auth_missing", "invalid_auth", "auth_expired", "challenge", "route_unconfigured",
 "channel_mismatch", "export_unverified", "download_failed", "workbook_invalid", "quota_unknown", "quota_low",
 "active_session", "session_failed", "closure_unconfirmed", "sync_failed", "auth_revoked", "acceptance_required",
 "duplicate_run", "studio_unavailable"]);

export function workerOrigin(value) {
 const url = new URL(value);
 if (url.origin !== "https://" + url.hostname || url.pathname !== "/" || url.search || url.hash || url.username ||
     url.password || !/^zen-editorial-control\.[a-z0-9-]+\.workers\.dev$/.test(url.hostname))
  throw Error("studio_unavailable");
 return url.origin;
}

export async function bridge(origin, secret, action, payload, fetcher = fetch) {
 if (!/^[a-f0-9]{64}$/i.test(secret || "")) throw Error("studio_unavailable");
 const response = await fetcher(workerOrigin(origin) + "/internal/zen-studio/" + action, {
  method: "POST", headers: {Authorization: "Bearer " + secret, "Content-Type": "application/json"},
  body: JSON.stringify(payload), redirect: "error", signal: AbortSignal.timeout(12000),
 });
 const result = await response.json();
 if (!response.ok) throw Error(SAFE.has(result?.error) ? result.error : "studio_unavailable");
 return result;
}

// Statistics routes per channel: one JSON secret DZEN_STUDIO_ROUTES ({"<channel>": "<url>"}), so a new
// registry channel needs no workflow change. Legacy per-channel DZEN_<KEY>_STATS_URL values still work.
export function channelRoutes(env) {
 let configured = {};
 if (env.DZEN_STUDIO_ROUTES) {
  try { configured = JSON.parse(env.DZEN_STUDIO_ROUTES); } catch { throw Error("route_unconfigured"); }
  if (!configured || typeof configured !== "object" || Array.isArray(configured)) throw Error("route_unconfigured");
 }
 return Object.fromEntries(STUDIO_CHANNELS.map(channel =>
  [channel, configured[channel] || env["DZEN_" + channel.toUpperCase() + "_STATS_URL"]]));
}

export function routeConfig(env, mode) {
 const channels = validChannel(mode) ? [mode] : STUDIO_CHANNELS;
 let selected;
 try {
  const urls = channelRoutes(env);
  selected = Object.fromEntries(channels.map(channel => [channel, studioRoute(urls[channel])]));
  if (new Set(Object.values(selected).map(route => route.id)).size !== channels.length) throw Error();
 } catch { throw Error("route_unconfigured"); }
 const path = env.DZEN_STUDIO_EXPORT_PATH || "";
 const name = env.DZEN_STUDIO_EXPORT_LABEL?.trim() || "";
 if (!/^\/[a-zA-Z0-9_/-]{3,240}$/.test(path) || path.includes("//") || name.length < 3 || name.length > 120)
  throw Error("export_unverified");
 return {selected, path, name};
}

export async function assertStudio(page, route) {
 const where = new URL(page.url());
 const authOrigin = /^https:\/\/(?:passport\.yandex\.(?:ru|com)|id\.yandex\.ru|id\.vk\.(?:com|ru))$/.test(where.origin);
 const body = page.locator("body");
 const text = await body.innerText({timeout: 2500});
 if (/showcaptcha|captcha|challenge/i.test(where.pathname) ||
     /подтвердите, что вы не робот|проверка безопасности|введите символы с картинки|verify you are human/i.test(text))
  throw Error("challenge");
 const login = page.getByRole("button", {name: "Войти", exact: true})
  .or(page.getByRole("link", {name: "Войти", exact: true})).filter({visible: true});
 if (authOrigin || (where.origin === ORIGIN &&
     (/\/auth(?:\/|$)|\/login(?:\/|$)/i.test(where.pathname) ||
     /войти в дзен|войти с яндекс id|введите номер телефона/i.test(text) || await login.count() > 0)))
  throw Error("auth_expired");
 // Dzen appends view parameters (?statType=...) to the statistics page; the channel
 // identity is the exact origin and path, so compare those and ignore the query.
 if (where.origin + where.pathname !== route.url || where.username || where.password) throw Error("channel_mismatch");
}

async function takeWorkbook(cdp, event) {
 const type = event.responseHeaders?.find(header => header.name.toLowerCase() === "content-type")?.value || "";
 const disposition = event.responseHeaders?.find(header => header.name.toLowerCase() === "content-disposition")?.value || "";
 if (event.responseStatusCode !== 200 || !(/application\/vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet/i.test(type) ||
     // Dzen serves its XLSX report as application/vnd.ms-excel with an .xlsx attachment name;
     // the importer still validates the workbook structure, channel and period.
     (/application\/(?:octet-stream|vnd\.ms-excel)/i.test(type) && /\.xlsx(?:["';\s]|$)/i.test(disposition)))) {
  await cdp.send("Fetch.continueResponse", {requestId: event.requestId});
  return null;
 }
 const {stream} = await cdp.send("Fetch.takeResponseBodyAsStream", {requestId: event.requestId});
 try {
  const chunks = [];
  let size = 0;
  for (;;) {
   const {data, base64Encoded, eof} = await cdp.send("IO.read", {handle: stream, size: 65536});
   const bytes = Buffer.from(data, base64Encoded ? "base64" : "utf8");
   size += bytes.length;
   if (size > MAX_XLSX) throw Error("download_failed");
   chunks.push(bytes);
   if (eof) break;
  }
  return Buffer.concat(chunks);
 } finally {
  try { await cdp.send("IO.close", {handle: stream}); } finally {
   await cdp.send("Fetch.fulfillRequest", {requestId: event.requestId, responseCode: 204, body: ""});
  }
 }
}

// Dzen opens a period menu whose confirm item repeats the control label ("Отчёт").
// Click it only when exactly one other visible element with that exact label appears;
// a single-step export (download on the first click) needs no confirmation.
export async function confirmReportMenu(page, name, control, done, {attempts = 12, pause = sleep} = {}) {
 const origin = await control.first().boundingBox().catch(() => null);
 for (let i = 0; i < attempts && !done(); i++) {
  await pause(250);
  const items = page.getByText(name, {exact: true}).filter({visible: true});
  const others = [];
  for (let j = 0; j < await items.count(); j++) {
   const box = await items.nth(j).boundingBox().catch(() => null);
   const cx = box && box.x + box.width / 2, cy = box && box.y + box.height / 2;
   const inside = origin && box && cx >= origin.x && cx <= origin.x + origin.width && cy >= origin.y && cy <= origin.y + origin.height;
   if (box && !inside) others.push(j);
  }
  if (others.length > 1) throw Error("export_unverified");
  if (others.length === 1) {
   await items.nth(others[0]).click({timeout: 4000, noWaitAfter: true});
   return true;
  }
 }
 return false;
}

// Dzen shows onboarding dialogs (e.g. "Дочитывания") over statistics on some channels; they hide
// the report control from the accessibility tree. Close only dialogs, never navigate or confirm.
export async function dismissOverlays(page, {attempts = 3, pause = sleep} = {}) {
 for (let i = 0; i < attempts; i++) {
  const dialogs = page.locator("[role=dialog],[aria-modal=true]").filter({visible: true});
  if (await dialogs.count() === 0) return i > 0;
  await page.keyboard.press("Escape").catch(() => {});
  await pause(300);
  if (await dialogs.count() === 0) return true;
  const close = dialogs.first().getByRole("button", {name: /^(закрыть|close)$/i})
   .or(dialogs.first().locator("[aria-label*=акрыть],[aria-label*=lose]")).filter({visible: true});
  if (await close.count()) await close.first().click({timeout: 2000}).catch(() => {});
  await pause(300);
 }
 return false;
}

export async function workbook(context, page, path, name, origin = ORIGIN) {
 const button = page.getByRole("button", {name, exact: true});
 const link = page.getByRole("link", {name, exact: true});
 const control = button.or(link).filter({visible: true});
 await control.first().waitFor({state: "visible", timeout: 4000}).catch(() => { throw Error("export_unverified"); });
 if (await control.count() !== 1) throw Error("export_unverified");
 const cdp = await context.newCDPSession(page);
 let timer;
 let settle, reject;
 const result = new Promise((resolve, fail) => { settle = resolve; reject = fail; });
 // A rejected download can arrive before the click acknowledges.
 result.catch(() => {});
 let captured = false;
 const paused = async event => {
  try {
   const url = new URL(event.request.url);
   if (captured || url.origin !== origin || url.pathname !== path) {
    await cdp.send("Fetch.continueResponse", {requestId: event.requestId});
    return;
   }
   const bytes = await takeWorkbook(cdp, event);
   if (bytes && !captured) { captured = true; settle(bytes); }
  } catch { reject(Error("download_failed")); }
 };
 cdp.on("Fetch.requestPaused", paused);
 try {
  await cdp.send("Fetch.enable", {patterns: [{urlPattern: origin + path + "*", requestStage: "Response"}]});
  timer = setTimeout(() => reject(Error("download_failed")), 9000);
  await control.click({timeout: 4000, noWaitAfter: true});
  await confirmReportMenu(page, name, control, () => captured);
  return await result;
 } finally {
  clearTimeout(timer);
  cdp.off("Fetch.requestPaused", paused);
  try { await bounded(() => cdp.send("Fetch.disable"), 1000); }
  finally { await bounded(() => cdp.detach(), 1000); }
 }
}

export async function importWorkbook(bytes, channel, expectedId) {
 const child = spawn("python", ["-m", "src.zen_dzen_sync_import", channel, expectedId], {stdio: ["pipe", "pipe", "ignore"]});
 const output = [];
 const deadline = setTimeout(() => child.kill(), 3500);
 try {
  child.stdout.on("data", data => output.push(data));
  child.stdin.on("error", () => {});
  child.stdin.end(bytes);
  const [code] = await once(child, "close");
  if (code !== 0) throw Error("workbook_invalid");
  return JSON.parse(Buffer.concat(output).toString("utf8"));
 } catch { throw Error("workbook_invalid"); }
 finally { clearTimeout(deadline); }
}

function bounded(task, limit) {
 if (limit <= 0) return Promise.reject(Error("session_failed"));
 let timer;
 return Promise.race([Promise.resolve().then(task), new Promise((_, reject) => {
  timer = setTimeout(() => reject(Error("session_failed")), limit);
 })]).finally(() => clearTimeout(timer));
}

export async function cloudSession({client, state, states, routes, scan, now = Date.now, pause = sleep}) {
 const started = now();
 let attempted = false, browser, sessionId, error = null, rows;
 const deadline = started + WORK;
 const step = work => bounded(work, deadline - now());
 try {
  const acquired = await step(() => {
   attempted = true;
   return client.call(client.root + "?keep_alive=" + KEEP_ALIVE, "POST", 15000);
  });
  const sid = (acquired?.result || acquired)?.sessionId;
  if (!/^[a-f0-9-]{36}$/i.test(sid || "")) throw Error("session_failed");
  sessionId = sid;
  browser = await step(() => client.connect(sid));
  // Channels on different accounts never share cookies: one context per distinct session.
  const perChannel = states || Object.fromEntries(Object.keys(routes?.selected || {}).map(channel => [channel, state]));
  const contexts = new Map();
  const contextFor = {};
  for (const [channel, channelState] of Object.entries(perChannel)) {
   if (!contexts.has(channelState)) contexts.set(channelState, await step(() => browser.newContext({storageState: channelState,
     acceptDownloads: false, serviceWorkers: "block", timezoneId: "Europe/Moscow", viewport: {width: 1440, height: 900}})));
   contextFor[channel] = contexts.get(channelState);
  }
  const first = contexts.values().next().value;
  rows = await step(() => scan(first, routes, step, contextFor));
 } catch (failure) { error = SAFE.has(failure?.message) ? failure.message : "session_failed"; }
 let closed = !attempted;
 if (sessionId) {
  try {
   await confirmClose({sid: sessionId, root: client.root, call: client.call, pause, now,
    expiresAt: started + CAP - 1500,
    isActive: async (sid, timeout) => (await client.active(timeout)).includes(sid)});
   closed = true;
  } catch {}
 }
 if (browser) {
  try { await bounded(() => browser.close(), Math.min(1200, started + CAP - now())); } catch {}
 }
 const elapsedMs = now() - started;
 if (elapsedMs > CAP || !closed) error = "closure_unconfirmed";
 return {attempted, allocated: !!sessionId, closed: closed && elapsedMs <= CAP, elapsedMs, rows: error ? [] : rows, error};
}

async function scanStudio(context, {selected, path, name}, step, contextFor = {}) {
 const pages = await Promise.all(Object.entries(selected).map(async ([channel, route]) => {
  const page = await step(() => (contextFor[channel] || context).newPage());
  // The bare statistics route opens the subscribers tab; the XLSX report lives on the publications tab.
  const response = await step(() => page.goto(route.url + STATS_VIEW, {waitUntil: "domcontentloaded", timeout: 9000}));
  await step(() => assertStudio(page, route));
  if (!response?.ok()) throw Error("channel_mismatch");
  return {channel, route, page};
 }));
 return Promise.all(pages.map(async ({channel, route, page}) => {
  let bytes;
  try {
   await step(() => dismissOverlays(page));
   try { bytes = await step(() => workbook(page.context(), page, path, name)); }
   catch (failure) {
    // An onboarding dialog can appear after the page is ready and cover the control.
    // Retry once, only if a dialog was actually found and closed.
    if (failure?.message !== "export_unverified" || !await step(() => dismissOverlays(page))) throw failure;
    bytes = await step(() => workbook(page.context(), page, path, name));
   }
  }
  catch (error) { await step(() => assertStudio(page, route)); throw error; }
  await step(() => assertStudio(page, route));
  return step(() => importWorkbook(bytes, channel, route.id));
 }));
}

export function cloudflare(env) {
 const id = env.CF_ACCOUNT_ID, token = env.CF_API_TOKEN, quotaToken = env.CF_WORKERS_TOKEN;
 if (!/^[a-f0-9]{32}$/i.test(id || "") || !token || !quotaToken) throw Error("quota_unknown");
 const root = "https://api.cloudflare.com/client/v4/accounts/" + id + "/browser-run/devtools/browser";
 const headers = {Authorization: "Bearer " + token};
 const call = async (url, method, timeout = 5000) => {
  try {
   const response = await fetch(url, {method, headers, redirect: "error", signal: AbortSignal.timeout(timeout)});
   if (!response.ok) throw Error("HTTP_" + response.status);
   return await response.json();
  } catch (error) {
   if (["TimeoutError", "AbortError"].includes(error.name)) throw Error("HTTP_TIMEOUT");
   throw error;
  }
 };
 return {
  root, call, connect: sid => require("playwright-core").chromium.connectOverCDP(
   root.replace("https:", "wss:") + "/" + sid, {headers, timeout: 13000}),
  async active(timeout) {
   const data = await call(root.replace(/\/browser$/, "/session") + "?limit=100", "GET", timeout);
   const rows = Array.isArray(data) ? data : data?.result;
   if (!Array.isArray(rows) || rows.length >= 100 || rows.some(row => typeof row?.sessionId !== "string"))
    throw Error("quota_unknown");
   return rows.map(row => row.sessionId);
  },
  async usage(day) {
   const result = await analytics({id, token: quotaToken, day, byHour: true});
   if (!result.enabled || result.errorCount !== 0 || !result.unsampled || !Number.isSafeInteger(result.reportedUsageMs))
    throw Error("quota_unknown");
   return result.reportedUsageMs;
  },
 };
}

export async function syncStudio({mode, env, request, client, scan = scanStudio, now = Date.now}) {
 const report = {mode, status: "skipped", browserAttempted: false, browserAllocated: false, usedMs: null, elapsedMs: null, closed: null};
 const run = crypto.randomUUID();
 let claimed = false, error = null, rows = [], channelIds = {};
 try {
  if (mode === "quota") {
   report.usedMs = await client.usage(UTC(now()));
   report.activeSessions = (await client.active()).length;
   report.status = "passed";
   return report;
  }
  const vault = await request("claim", {mode, run});
  claimed = true;
  if (!env.ZEN_STUDIO_VAULT_KEY) throw Error("auth_missing");
  const routes = routeConfig(env, mode);
  // Each channel's session is bound to the route of the channel it was enrolled for.
  const urls = channelRoutes(env);
  const opened = new Map();
  const states = {};
  for (const channel of Object.keys(routes.selected)) {
   const item = vault.envelopes?.[channel] || {envelope: vault.envelope, bound: PRIMARY_CHANNEL};
   if (!validChannel(item.bound)) throw Error("invalid_auth");
   const cacheKey = item.bound + ":" + (item.envelope?.ciphertext || "").slice(0, 32);
   if (!opened.has(cacheKey)) opened.set(cacheKey, await openStudioState(item.envelope, env.ZEN_STUDIO_VAULT_KEY,
    studioRoute(urls[item.bound]).url));
   states[channel] = opened.get(cacheKey);
  }
  const state = states[Object.keys(routes.selected)[0]];
  const day = UTC(now());
  report.usedMs = await client.usage(day);
  if ((await client.active()).length) throw Error("active_session");
  await request("reserve", {run, usedMs: report.usedMs, day});
  const result = await cloudSession({client, state, states, routes, scan, now});
  report.browserAttempted = result.attempted;
  report.browserAllocated = result.allocated;
  report.closed = result.closed;
  report.elapsedMs = result.elapsedMs;
  error = result.error;
  rows = result.rows;
  channelIds = Object.fromEntries(Object.entries(routes.selected).map(([channel, route]) => [channel, route.id]));
 } catch (failure) { error = SAFE.has(failure?.message) ? failure.message : "sync_failed"; }
 if (claimed) {
  try {
   const finish = await request("finish", {run, closed: report.closed, elapsedMs: report.elapsedMs,
    error, channelIds, reports: rows});
   if (!finish.ok) error = SAFE.has(finish.error) ? finish.error : "sync_failed";
  } catch { error = "sync_failed"; }
 }
 report.error = error;
 report.status = error ? (report.browserAttempted ? "failed" : "skipped") : "passed";
 return report;
}

async function main() {
 const env = process.env;
 const mode = env.ZEN_STUDIO_MODE;
 if (!(["all", "hourly", "quota"].includes(mode) || validChannel(mode))) throw Error("sync_failed");
 const request = (action, data) => bridge(env.ZEN_STUDIO_WORKER_URL, env.ZEN_STUDIO_BRIDGE_SECRET, action, data);
 const report = await syncStudio({mode, env, request, client: cloudflare(env)});
 console.log(JSON.stringify({...report, dailyMs: DAILY_MS, sessionMs: CAP, keepAliveMs: KEEP_ALIVE,
  reservationMs: RESERVATION_MS}));
 if (report.status === "failed" || (report.status === "skipped" && mode !== "hourly")) process.exitCode = 1;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
 main().catch(error => {
  console.log(JSON.stringify({status: "skipped", browserAllocated: false,
   error: SAFE.has(error?.message) ? error.message : "sync_failed"}));
  process.exitCode = 1;
 });
}
