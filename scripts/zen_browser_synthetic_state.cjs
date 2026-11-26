// Two independent Cloudflare sessions with a local synthetic fixture.
const {writeFile} = require("node:fs/promises");
const {confirmClose} = require("./zen_browser_close.cjs");

const LIMITS = Object.freeze({
  dailyMs: 480000, sessionMs: 60000, keepAliveMs: 10000, admissionMs: 90000,
});
const ORIGIN = "https://example.invalid";
const KEY = "synthetic_state";
const VALUE = "test-only";
const ERROR_CODES = new Set([
  "CONFIG_MISSING", "ACCOUNT_USAGE_UNAVAILABLE", "INVALID_HISTORY", "HISTORY_INCOMPLETE", "INVALID_START",
  "ACTIVE_OR_INVALID_SESSION", "BUDGET_STOP", "SESSION_ID_MISSING",
  "UNEXPECTED_STATE", "RESTORE_FAILED", "WATCHDOG", "CLOSE_UNCONFIRMED",
  "UNKNOWN_CLOSE_STATE", "NOT_NORMAL_CLOSURE", "HTTP_TIMEOUT",
]);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function safeError(error) {
  const message = error?.message;
  return ERROR_CODES.has(message) || /^HTTP_[1-5]\d{2}$/.test(message || "")
    ? message : "UNEXPECTED";
}

function within(work, ms) {
  let timer;
  return Promise.race([
    Promise.resolve().then(work),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(Error("WATCHDOG")), Math.max(1, ms));
    }),
  ]).finally(() => clearTimeout(timer));
}

function sessionList(data) {
  const list = Array.isArray(data) ? data : data.result;
  if (!Array.isArray(list)) throw Error("INVALID_HISTORY");
  return list;
}

async function history(list) {
  const rows = [], seen = new Set();
  for (let offset = 0; offset < 10000; offset += 100) {
    const page = await list(offset);
    for (const row of page) {
      if (typeof row.sessionId !== "string" || seen.has(row.sessionId)) throw Error("INVALID_HISTORY");
      seen.add(row.sessionId);
      rows.push(row);
    }
    if (page.length < 100) return rows;
  }
  throw Error("HISTORY_INCOMPLETE");
}

async function admit(list, count, now, report) {
  const rows = await history(list), observed = now(), day = new Date(observed);
  day.setUTCHours(0, 0, 0, 0);
  let usedMs = 0;
  for (const row of rows) {
    if (!Number.isFinite(row.startTime) || row.startTime < 1e12 || row.startTime > observed) {
      throw Error("INVALID_START");
    }
    if (!Number.isFinite(row.endTime) || row.endTime < row.startTime || row.endTime > observed + 5000) {
      throw Error("ACTIVE_OR_INVALID_SESSION");
    }
    usedMs += Math.max(0, Math.min(observed, row.endTime) - Math.max(day.getTime(), row.startTime));
  }
  const reservedMs = LIMITS.admissionMs * count;
  const admitted = usedMs + reservedMs <= LIMITS.dailyMs && observed + reservedMs < day.getTime() + 86400000;
  report.admission.push({usedMs, reservedMs, admitted});
  if (!admitted) throw Error("BUDGET_STOP");
}

async function session({state, first, root, call, connect, list, now, wait, report, workMs}) {
  const started = now();
  const deadline = started + LIMITS.sessionMs;
  const workDeadline = started + workMs;
  const result = {phase: first ? "save" : "restore", stage: "acquire",
                  browserAllocated: false, outcome: "failed", cleanup: {}, checks: {}};
  report.sessions.push(result);
  let sid, browser;
  let failure;
  const step = (stage, work) => {
    result.stage = stage;
    if (now() >= workDeadline) throw Error("WATCHDOG");
    return within(work, workDeadline - now());
  };
  try {
    const data = await step("acquire", () => call(root + "?keep_alive=" + LIMITS.keepAliveMs, "POST", 15000));
    const acquired = (data.result || data).sessionId;
    if (!/^[a-f0-9-]{36}$/i.test(acquired || "")) throw Error("SESSION_ID_MISSING");
    sid = acquired;
    result.browserAllocated = true;
    browser = await step("connect", () => connect(root.replace("https:", "wss:") + "/" + sid));
    const ctx = await step("context", () => browser.newContext(first ? {} : {storageState: state}));
    const page = await step("page", () => ctx.newPage());
    await step("route", () => page.route("**/*", route => route.request().url() === ORIGIN + "/"
      ? route.fulfill({status: 200, contentType: "text/html", body: "<!doctype html><title>Synthetic test</title>"})
      : route.abort()));
    await step("navigate", () => page.goto(ORIGIN + "/", {waitUntil: "domcontentloaded", timeout: 5000}));
    if (first) {
      await step("cookie_write", () => ctx.addCookies([{name: KEY, value: VALUE, url: ORIGIN}]));
      await step("local_write", () => page.evaluate(([key, value]) => localStorage.setItem(key, value), [KEY, VALUE]));
      const saved = await step("capture", () => ctx.storageState());
      const [cookie] = saved.cookies;
      const [origin] = saved.origins;
      result.checks.cookie = saved.cookies.length === 1 && cookie.name === KEY
        && cookie.value === VALUE && cookie.domain === "example.invalid";
      result.checks.localStorage = saved.origins.length === 1 && origin.origin === ORIGIN
        && origin.localStorage.length === 1 && origin.localStorage[0].name === KEY
        && origin.localStorage[0].value === VALUE;
      if (!result.checks.cookie || !result.checks.localStorage) throw Error("UNEXPECTED_STATE");
      return saved;
    }
    const cookies = await step("verify_cookie", () => ctx.cookies(ORIGIN));
    const local = await step("verify_local", () => page.evaluate(key => localStorage.getItem(key), KEY));
    result.checks.cookie = cookies.length === 1 && cookies[0].name === KEY && cookies[0].value === VALUE;
    result.checks.localStorage = local === VALUE;
    if (!result.checks.cookie || !result.checks.localStorage) throw Error("RESTORE_FAILED");
  } catch (error) {
    failure = error;
    result.error = safeError(error);
    throw error;
  } finally {
    let closeFailure;
    if (sid) {
      try {
        await confirmClose({
          sid, root, call, pause: wait, now, expiresAt: deadline - 1500,
          latest: timeout => list(0, timeout),
          observe: event => { result.cleanup[event] = (result.cleanup[event] || 0) + 1; },
          onDuration: duration => { result.browserDurationMs = duration; },
        });
        result.cleanup.confirmed = true;
      } catch (error) {
        closeFailure = error;
        result.cleanup.error = safeError(error);
      }
    }
    if (browser) {
      try {
        await within(() => browser.close(), Math.min(1200, deadline - now()));
      } catch {
        result.cleanup.cdpDisconnectUnconfirmed = true;
      }
    }
    result.elapsedMs = now() - started;
    if ((result.elapsedMs > LIMITS.sessionMs || result.browserDurationMs > LIMITS.sessionMs)
        && !closeFailure) closeFailure = Error("WATCHDOG");
    if (!failure && closeFailure) {
      result.stage = "close";
      result.error = safeError(closeFailure);
      throw closeFailure;
    }
    if (!failure && result.cleanup.confirmed) result.outcome = "passed";
  }
}

async function runProbe({root, call, connect, listHistory, now = Date.now, wait = pause, workMs = 33000} = {}) {
  const report = {schema: "zen-browser-synthetic-diagnostics/v1", limits: LIMITS,
                  status: "failed", stage: "configuration", admission: [], sessions: []};
  try {
    if (!root || !call || !connect) throw Error("CONFIG_MISSING");
    const list = async (offset, timeout) => {
      if (!listHistory) throw Error("ACCOUNT_USAGE_UNAVAILABLE");
      return sessionList(await listHistory(offset, timeout));
    };
    const config = {root, call, connect, list, now, wait, workMs};
    report.stage = "save_admission";
    await admit(list, 2, now, report);
    report.stage = "save";
    const state = await session({...config, first: true, report});
    report.stage = "restore_admission";
    await admit(list, 1, now, report);
    report.stage = "restore";
    await session({...config, first: false, state, report});
    report.stage = "complete";
    report.status = "passed";
  } catch (error) {
    report.error = safeError(error);
  }
  return report;
}

async function main() {
  const id = process.env.CF_ACCOUNT_ID, token = process.env.CF_API_TOKEN;
  const config = {};
  if (/^[a-f0-9]{32}$/i.test(id || "") && token) {
    const headers = {Authorization: "Bearer " + token};
    config.root = "https://api.cloudflare.com/client/v4/accounts/" + id + "/browser-run/devtools/browser";
    config.call = async (url, method = "GET", timeout = 5000) => {
      try {
        const response = await fetch(url, {method, headers, redirect: "error", signal: AbortSignal.timeout(timeout)});
        if (!response.ok) throw Error("HTTP_" + response.status);
        return await response.json();
      } catch (error) {
        if (error.name === "TimeoutError" || error.name === "AbortError") throw Error("HTTP_TIMEOUT");
        throw error;
      }
    };
    config.connect = url => require("playwright-core").chromium.connectOverCDP(url, {headers, timeout: 14000});
  }
  const report = await runProbe(config);
  const encoded = JSON.stringify(report, null, 2);
  if (process.env.ZEN_BROWSER_DIAGNOSTIC_PATH) {
    await writeFile(process.env.ZEN_BROWSER_DIAGNOSTIC_PATH, encoded + "\n", {mode: 0o600});
  }
  console.log(encoded);
  if (report.status !== "passed") process.exitCode = 1;
}

if (require.main === module) {
  main().catch(error => { console.error(safeError(error)); process.exitCode = 1; });
}
module.exports = {LIMITS, runProbe, safeError};
