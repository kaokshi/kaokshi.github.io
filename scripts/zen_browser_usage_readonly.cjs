// Read-only discovery of a usage source. Output is restricted to schema facts.
const {writeFileSync} = require("node:fs");
const id = process.env.CF_ACCOUNT_ID;
const token = process.env.CF_API_TOKEN;
const report = {schema: "zen-browser-synthetic-diagnostics/v1", status: "read_only",
                browserAllocated: false, activeSessions: {}, analytics: {}};
const headers = {Authorization: "Bearer " + token, "Content-Type": "application/json"};
const origin = "https://api.cloudflare.com/client/v4";
async function probe() {
  if (!/^[a-f0-9]{32}$/i.test(id || "") || !token) {
    report.status = "configuration_missing";
    return;
  }
  const session = await fetch(origin + "/accounts/" + id + "/browser-run/devtools/session?limit=100",
    {headers, redirect: "error", signal: AbortSignal.timeout(10000)});
  report.activeSessions.httpStatus = session.status;
  if (session.ok) {
    const payload = await session.json();
    const rows = Array.isArray(payload) ? payload : payload.result;
    if (Array.isArray(rows)) report.activeSessions.count = rows.length;
  }
  const query = `query BrowserUsage($account: string, $day: Date) {
    viewer {
      accounts(filter: {accountTag: $account}) {
        settings { browserRenderingBrowserTimeUsageAdaptiveGroups { enabled } }
        browserRenderingBrowserTimeUsageAdaptiveGroups(limit: 1, filter: {date: $day}) {
          count
          sum { totalSessionDurationMs }
        }
      }
    }
  }`;
  const response = await fetch(origin + "/graphql", {method: "POST", headers,
    body: JSON.stringify({query, variables: {account: id, day: new Date().toISOString().slice(0, 10)}}),
    redirect: "error", signal: AbortSignal.timeout(10000)});
  report.analytics.httpStatus = response.status;
  if (!response.ok) return;
  const payload = await response.json();
  const errors = Array.isArray(payload.errors) ? payload.errors : [];
  report.analytics.errorCount = errors.length;
  report.analytics.errorKinds = [...new Set(errors.map(({message = ""}) => {
    if (/permission|authenticated|authori[sz]|not allowed|access|does not have/i.test(message)) return "permission";
    if (/unknown|not defined|cannot query|does not exist|not found/i.test(message)) return "schema";
    if (/parse|type|invalid|expected|argument/i.test(message)) return "query";
    return "other";
  }))];
  const account = payload.data?.viewer?.accounts?.[0];
  const dataset = "browserRenderingBrowserTimeUsageAdaptiveGroups";
  report.analytics.enabled = account?.settings?.[dataset]?.enabled === true;
  const [usage] = account?.[dataset] || [];
  if (Number.isSafeInteger(usage?.count) && Number.isSafeInteger(usage?.sum?.totalSessionDurationMs)) {
    report.analytics.sessionCount = usage.count;
    report.analytics.reportedUsageMs = usage.sum.totalSessionDurationMs;
  }
}
probe().catch(() => { report.status = "read_only_error"; }).finally(() => {
  const data = JSON.stringify(report, null, 2);
  writeFileSync(process.env.ZEN_BROWSER_DIAGNOSTIC_PATH, data + "\n", {mode: 0o600});
  console.log(data);
});
