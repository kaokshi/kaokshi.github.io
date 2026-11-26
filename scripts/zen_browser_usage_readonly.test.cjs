const {test} = require("node:test");
const assert = require("node:assert/strict");
const {analytics} = require("./zen_browser_usage_readonly.cjs");

const dataset = "browserRenderingBrowserTimeUsageAdaptiveGroups";
const confidence = {lower: 1234, upper: 1234, sampleSize: 2};
const row = {count: 2, sum: {totalSessionDurationMs: 1234},
             confidence: {count: {sampleSize: 2}, sum: {totalSessionDurationMs: confidence}}};

function requestWith(rows, errors) {
  return async (url, options) => {
    assert.equal(url, "https://api.cloudflare.com/client/v4/graphql");
    assert.equal(options.method, "POST");
    assert.equal(options.redirect, "error");
    assert.deepEqual(JSON.parse(options.body).variables, {account: "fixture-id", day: "2026-11-26"});
    return {ok: true, status: 200, json: async () => ({errors, data: {viewer: {accounts: [
      {settings: {[dataset]: {enabled: true}}, [dataset]: rows},
    ]}}})};
  };
}

async function usage(rows, errors) {
  return analytics({id: "fixture-id", token: "fixture-secret", day: "2026-11-26",
                    byHour: true, request: requestWith(rows, errors)});
}

test("accepts unsampled totals and an explicit empty result", async () => {
  const measured = await usage([row]);
  assert.equal(measured.reportedUsageMs, 1234);
  assert.equal(measured.unsampled, true);
  assert.equal((await usage([])).reportedUsageMs, 0);
});

test("identifies a sampled or incomplete usage result", async () => {
  const sampled = await usage([{...row, count: 3}]);
  assert.equal(sampled.unsampled, false);
  const missing = await usage(undefined);
  assert.equal(missing.reportedUsageMs, undefined);
});

test("classifies an authentication failure without logging the provider response", async () => {
  const report = await usage(undefined, [{message: "permission denied for fixture-secret"}]);
  assert.deepEqual(report.errorKinds, ["permission"]);
  assert.doesNotMatch(JSON.stringify(report), /fixture-secret|fixture-id/);
});
