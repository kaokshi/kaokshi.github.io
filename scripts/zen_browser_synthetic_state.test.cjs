const {test} = require("node:test");
const assert = require("node:assert/strict");
const {LIMITS, runProbe, safeError} = require("./zen_browser_synthetic_state.cjs");

const root = "https://example.invalid/devtools/browser";
const secret = "SYNTHETICSECRETTHATMUSTNOTBELOGGED";
const start = Date.UTC(2026, 10, 26, 12);
const sid = "00000000-0000-4000-8000-00000000000";

function fixture({failure, previous = 0, unconfirmed = false} = {}) {
  let acquired = 0, disconnected = 0;
  const alive = new Map(), actions = [];
  const call = async (url, method) => {
    if (method === "POST") {
      assert.ok(url.endsWith("?keep_alive=10000"));
      assert.equal(alive.size, 0);
      const sessionId = sid + (++acquired);
      alive.set(sessionId, {sessionId, startTime: start - 10});
      actions.push("acquire");
      return {result: {sessionId, authorization: secret}};
    }
    const id = url.split("/").at(-1);
    if (method === "DELETE") {
      const value = alive.get(id);
      assert.ok(value);
      if (!unconfirmed) alive.delete(id);
      actions.push("delete");
      return {status: "closing"};
    }
    throw Error("HTTP_404");
  };
  const connect = async () => {
    assert.equal(alive.size, 1);
    if (failure === "connect") throw Error(secret);
    return {
      newContext: async ({storageState}) => {
        let cookies = storageState?.cookies || [], state = storageState?.origins[0].localStorage[0].value;
        return {
          newPage: async () => ({
            route: async () => {},
            goto: async () => {
              if (failure === "navigate") throw Error(secret);
              if (failure === "watchdog") return new Promise(() => {});
            },
            evaluate: async (_, params) => {
              if (Array.isArray(params)) state = params[1];
              else return state;
            },
          }),
          addCookies: async rows => { cookies = rows.map(row => ({...row, domain: "example.invalid"})); },
          cookies: async () => cookies,
          storageState: async () => ({
            cookies, origins: [{origin: "https://example.invalid",
                                localStorage: [{name: "synthetic_state", value: state}]}],
          }),
        };
      },
      close: async () => { disconnected++; },
    };
  };
  return {options: {root, call, connect, readUsage: async () => previous,
                    listActive: async () => [...alive.keys()], now: () => start, wait: async () => {}},
          actions, counts: () => ({acquired, disconnected})};
}

test("captures and restores in a separate browser only after explicit closure", async () => {
  const f = fixture();
  const report = await runProbe(f.options);
  assert.equal(report.status, "passed");
  assert.deepEqual(f.actions, ["acquire", "delete", "acquire", "delete"]);
  assert.equal(f.counts().disconnected, 2);
  assert.ok(report.sessions.every(entry => entry.cleanup.removed_from_active_list === 1
    && entry.cleanup.confirmed && entry.checks.cookie && entry.checks.localStorage));
  assert.equal(report.limits.dailyMs, 480000);
  assert.equal(report.limits.sessionMs, 60000);
  assert.equal(report.limits.keepAliveMs, 10000);
  assert.doesNotMatch(JSON.stringify(report), new RegExp(secret + "|00000000|test-only|example.invalid"));
});

test("stage failure is redacted and triggers explicit cleanup", async () => {
  const f = fixture({failure: "navigate"});
  const report = await runProbe(f.options);
  assert.equal(report.status, "failed");
  assert.equal(report.sessions[0].stage, "navigate");
  assert.equal(report.sessions[0].error, "UNEXPECTED");
  assert.equal(report.sessions[0].cleanup.confirmed, true);
  assert.deepEqual(f.counts(), {acquired: 1, disconnected: 1});
  assert.doesNotMatch(JSON.stringify(report), new RegExp(secret));
  assert.equal(safeError(Error(secret)), "UNEXPECTED");
});

test("failed connection still triggers forced closure", async () => {
  const f = fixture({failure: "connect"});
  const report = await runProbe(f.options);
  assert.equal(report.sessions[0].stage, "connect");
  assert.deepEqual(f.actions, ["acquire", "delete"]);
});

test("unconfirmed closure prevents allocation of the second browser", async () => {
  const f = fixture({unconfirmed: true});
  const report = await runProbe(f.options);
  assert.equal(report.error, "CLOSE_UNCONFIRMED");
  assert.equal(report.sessions.length, 1);
  assert.equal(f.counts().acquired, 1);
});

test("the two-session reservation is rejected before cloud allocation", async () => {
  const f = fixture({previous: LIMITS.dailyMs - 2 * LIMITS.admissionMs + 1});
  const report = await runProbe(f.options);
  assert.equal(report.error, "BUDGET_STOP");
  assert.deepEqual(f.actions, []);
});

test("an active-only session list cannot substitute for account usage", async () => {
  const f = fixture();
  const report = await runProbe({...f.options, readUsage: undefined});
  assert.equal(report.error, "ACCOUNT_USAGE_UNAVAILABLE");
  assert.deepEqual(f.actions, []);
});

test("a second allocation keeps the first reservation even if analytics lags", async () => {
  const f = fixture();
  const totals = [LIMITS.dailyMs - 2 * LIMITS.admissionMs,
                  LIMITS.dailyMs - 2 * LIMITS.admissionMs + 1];
  const report = await runProbe({...f.options, readUsage: async day => {
    assert.equal(day, "2026-11-26");
    return totals.shift();
  }});
  assert.equal(report.error, "BUDGET_STOP");
  assert.equal(report.admission[1].previouslyReservedMs, LIMITS.admissionMs);
  assert.deepEqual(f.actions, ["acquire", "delete"]);
});

test("active account sessions block admission", async () => {
  const f = fixture();
  const report = await runProbe({...f.options, listActive: async () => ["active"]});
  assert.equal(report.error, "ACTIVE_SESSION");
  assert.deepEqual(f.actions, []);
});

test("a hung browser action triggers the watchdog and still closes explicitly", async () => {
  const f = fixture({failure: "watchdog"});
  const report = await runProbe({...f.options, workMs: 20});
  assert.equal(report.error, "WATCHDOG");
  assert.equal(report.sessions[0].stage, "navigate");
  assert.equal(report.sessions[0].cleanup.confirmed, true);
  assert.deepEqual(f.actions, ["acquire", "delete"]);
});
