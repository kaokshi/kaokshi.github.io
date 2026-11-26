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
  const query = "{ __type(name: \"Account\") { fields { name type { name ofType { name } } } } }";
  const response = await fetch(origin + "/graphql", {method: "POST", headers,
    body: JSON.stringify({query}), redirect: "error", signal: AbortSignal.timeout(10000)});
  report.analytics.httpStatus = response.status;
  if (!response.ok) return;
  const payload = await response.json();
  const fields = payload.data?.__type?.fields;
  report.analytics.errorCount = Array.isArray(payload.errors) ? payload.errors.length : 0;
  report.analytics.hasSchema = Array.isArray(fields);
  report.analytics.hasBrowserUsage = Array.isArray(fields) && fields.some(field => /browser/i.test(field.name));
}
probe().catch(() => { report.status = "read_only_error"; }).finally(() => {
  const data = JSON.stringify(report, null, 2);
  writeFileSync(process.env.ZEN_BROWSER_DIAGNOSTIC_PATH, data + "\n", {mode: 0o600});
  console.log(data);
});
