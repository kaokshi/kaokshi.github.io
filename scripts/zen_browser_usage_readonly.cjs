// Read-only discovery of a usage source. Output is restricted to schema facts.
const {writeFileSync} = require("node:fs");
const id = process.env.CF_ACCOUNT_ID;
const token = process.env.CF_API_TOKEN;
const report = {schema: "zen-browser-synthetic-diagnostics/v1", status: "read_only",
                browserAllocated: false, activeSessions: {}, analytics: {}};
const authorization = token => ({Authorization: "Bearer " + token, "Content-Type": "application/json"});
const origin = "https://api.cloudflare.com/client/v4";
async function probe() {
  if (!/^[a-f0-9]{32}$/i.test(id || "") || !token) {
    report.status = "configuration_missing";
    return;
  }
  const session = await fetch(origin + "/accounts/" + id + "/browser-run/devtools/session?limit=100",
    {headers: authorization(token), redirect: "error", signal: AbortSignal.timeout(10000)});
  report.activeSessions.httpStatus = session.status;
  if (session.ok) {
    const payload = await session.json();
    const rows = Array.isArray(payload) ? payload : payload.result;
    if (Array.isArray(rows)) report.activeSessions.count = rows.length;
  }
  const day = new Date().toISOString().slice(0, 10);
  report.analytics.browser = await analytics({id, token, day});
  if (!report.analytics.browser.enabled && process.env.CF_WORKERS_TOKEN) {
    const credentials = {id, token: process.env.CF_WORKERS_TOKEN, day};
    report.analytics.workers = await analytics(credentials);
    if (report.analytics.workers.enabled) {
      report.analytics.byHour = await analytics({...credentials, byHour: true});
    }
  }
}

async function analytics({id, token, day, byHour = false, request = fetch}) {
  const result = {};
  const query = `query BrowserUsage($account: string, $day: Date) {
    viewer {
      accounts(filter: {accountTag: $account}) {
        settings { browserRenderingBrowserTimeUsageAdaptiveGroups { enabled } }
        browserRenderingBrowserTimeUsageAdaptiveGroups(limit: ${byHour ? 24 : 1}, filter: {date: $day, sessionDurationMs_gt: 0}) {
          ${byHour ? "dimensions { datetimeHour }" : ""}
          count
          sum { totalSessionDurationMs }
          confidence(level: 0.95) {
            count { estimate lower upper sampleSize }
            sum { totalSessionDurationMs { estimate lower upper sampleSize } }
          }
        }
      }
    }
  }`;
  const response = await request(origin + "/graphql", {method: "POST", headers: authorization(token),
    body: JSON.stringify({query, variables: {account: id, day}}),
    redirect: "error", signal: AbortSignal.timeout(10000)});
  result.httpStatus = response.status;
  if (!response.ok) return result;
  const payload = await response.json();
  const errors = Array.isArray(payload.errors) ? payload.errors : [];
  result.errorCount = errors.length;
  result.errorKinds = [...new Set(errors.map(({message = ""}) => {
    if (/permission|authenticated|authori[sz]|not allowed|access|does not have/i.test(message)) return "permission";
    if (/unknown|not defined|cannot query|does not exist|not found/i.test(message)) return "schema";
    if (/parse|type|invalid|expected|argument/i.test(message)) return "query";
    return "other";
  }))];
  const accounts = payload.data?.viewer?.accounts;
  const account = accounts?.length === 1 ? accounts[0] : undefined;
  const dataset = "browserRenderingBrowserTimeUsageAdaptiveGroups";
  result.enabled = account?.settings?.[dataset]?.enabled === true;
  const rows = account?.[dataset];
  if (Array.isArray(rows) && rows.length === 0 && result.enabled && !errors.length) {
    return {...result, sessionCount: 0, reportedUsageMs: 0, unsampled: true};
  }
  if (Array.isArray(rows) && rows.length && rows.every(usage =>
    Number.isSafeInteger(usage?.count) && Number.isSafeInteger(usage?.sum?.totalSessionDurationMs))) {
    result.sessionCount = rows.reduce((total, usage) => total + usage.count, 0);
    result.reportedUsageMs = rows.reduce((total, usage) => total + usage.sum.totalSessionDurationMs, 0);
    result.unsampled = rows.every(usage => usage.confidence?.count?.sampleSize === usage.count
      && usage.confidence?.sum?.totalSessionDurationMs?.sampleSize === usage.count
      && usage.confidence?.sum?.totalSessionDurationMs?.upper === usage.sum.totalSessionDurationMs
      && usage.confidence?.sum?.totalSessionDurationMs?.lower === usage.sum.totalSessionDurationMs);
    const upper = rows.map(usage => usage.confidence?.sum?.totalSessionDurationMs?.upper);
    if (upper.every(Number.isFinite)) result.confidenceUpperMs = upper.reduce((total, value) => total + value, 0);
    if (byHour) result.groups = rows.length;
  }
  return result;
}

if (require.main === module) {
  probe().catch(() => { report.status = "read_only_error"; }).finally(() => {
    const data = JSON.stringify(report, null, 2);
    writeFileSync(process.env.ZEN_BROWSER_DIAGNOSTIC_PATH, data + "\n", {mode: 0o600});
    console.log(data);
  });
}
module.exports = {analytics};
