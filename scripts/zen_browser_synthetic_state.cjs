// Two independent Cloudflare sessions with a local synthetic fixture.
const {writeFile} = require("node:fs/promises");
const {confirmClose} = require("./zen_browser_close.cjs");
const {analytics} = require("./zen_browser_usage_readonly.cjs");

const LIMITS = Object.freeze({
  dailyMs: 480000, sessionMs: 60000, keepAliveMs: 10000, admissionMs: 90000,
  acquisitionIntervalMs: 20000,
});
const ORIGIN = "https://example.invalid";
const KEY = "synthetic_state";
const VALUE = "test-only";
const ERROR_CODES = new Set([
  "CONFIG_MISSING", "ACCOUNT_USAGE_UNAVAILABLE", "INVALID_ACTIVE_LIST",
  "ACTIVE_SESSION", "BUDGET_STOP", "SESSION_ID_MISSING",
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

async function admit({readUsage, listActive, now, report, alreadyReserved = 0}) {
  if (!readUsage) throw Error("ACCOUNT_USAGE_UNAVAILABLE");
  const day = new Date(now());
  day.setUTCHours(0, 0, 0, 0);
  const date = day.toISOString().slice(0, 10);
  const usage = await readUsage(date);
  if (!Number.isSafeInteger(usage) || usage < 0) throw Error("ACCOUNT_USAGE_UNAVAILABLE");
  if ((await listActive()).length) throw Error("ACTIVE_SESSION");
  const usedMs = Math.max(usage, report.admission[0]?.usedMs ?? 0);
  const reservedMs = LIMITS.admissionMs * 2 - alreadyReserved;
  const timeToFinish = reservedMs + (alreadyReserved ? 0 : LIMITS.acquisitionIntervalMs);
  const admitted = usedMs + alreadyReserved + reservedMs <= LIMITS.dailyMs
    && now() + timeToFinish < day.getTime() + 86400000;
  report.admission.push({usedMs, previouslyReservedMs: alreadyReserved, reservedMs, admitted});
  if (!admitted) throw Error("BUDGET_STOP");
}

async function session({state, first, root, call, connect, listActive, now, wait, report, workMs}) {
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
          isActive: async (id, timeout) => (await listActive(timeout)).includes(id),
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

async function runProbe({root, call, connect, readUsage, listActive, now = Date.now, wait = pause, workMs = 33000} = {}) {
  const report = {schema: "zen-browser-synthetic-diagnostics/v1", limits: LIMITS,
                  status: "failed", stage: "configuration", admission: [], sessions: []};
  try {
    if (!root || !call || !connect || !listActive) throw Error("CONFIG_MISSING");
    const config = {root, call, connect, listActive, now, wait, workMs};
    report.stage = "save_admission";
    await admit({readUsage, listActive, now, report});
    report.stage = "save";
    const state = await session({...config, first: true, report});
    await wait(LIMITS.acquisitionIntervalMs);
    report.stage = "restore_admission";
    await admit({readUsage, listActive, now, report, alreadyReserved: LIMITS.admissionMs});
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
  const analyticsToken = process.env.CF_WORKERS_TOKEN;
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
    config.listActive = async timeout => {
      const data = await config.call(config.root.replace(/\/browser$/, "/session") + "?limit=100", "GET", timeout);
      const rows = Array.isArray(data) ? data : data?.result;
      if (!Array.isArray(rows) || rows.length >= 100 || rows.some(row => typeof row?.sessionId !== "string")) {
        throw Error("INVALID_ACTIVE_LIST");
      }
      return rows.map(row => row.sessionId);
    };
    if (analyticsToken) {
      config.readUsage = async day => {
        const usage = await analytics({id, token: analyticsToken, day, byHour: true});
        if (!usage.enabled || usage.errorCount !== 0 || !usage.unsampled) throw Error("ACCOUNT_USAGE_UNAVAILABLE");
        return usage.reportedUsageMs;
      };
    }
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
