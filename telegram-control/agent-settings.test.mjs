import {test} from "node:test";
import assert from "node:assert/strict";
import {CoreHandler} from "./core.mjs";
import {readAgentSettings,writeAgentSettings,initialAgentSettings,AGENT_MODELS,validateAgentSettings} from "./agent-settings.mjs";

const now = Date.parse("2026-11-27T09:00:00Z");
function fixture() {
 const store = new Map();
 const storage = {get: async k => store.get(k), put: async (k, v) => store.set(k, v), delete: async k => store.delete(k),
  list: async ({prefix}) => new Map([...store].filter(([k]) => k.startsWith(prefix))),
  transaction: async fn => fn(storage)};
 return {store, storage};
}
const post = (storage, body) => new CoreHandler({storage}).fetch(
 new Request("https://core.internal/core/agent/settings", {method: "POST", body: JSON.stringify(body)}));
const get = async storage => {
 const r = await new CoreHandler({storage}).fetch(new Request("https://core.internal/core/agent/settings", {method: "GET"}));
 assert.equal(r.status, 200);
 return r.json();
};

test("the managing agent starts on Claude Sonnet 5.5 with the current limits", async () => {
 const {storage} = fixture();
 const body = await get(storage);
 assert.equal(body.settings.model, "anthropic/claude-sonnet-5.5");
 assert.equal(body.settings.reasoning, "medium");
 assert.equal(body.settings.dailyRub, 30);
 assert.equal(body.settings.perCallRub, 5);
 assert.equal(body.settings.version, 0);
 // The owner may pick any of the offered models, including going back to Qwen.
 const ids = body.models.map(m => m.id);
 assert.ok(ids.includes("anthropic/claude-opus-5.5"));
 assert.ok(ids.includes("anthropic/claude-haiku-5.5"));
 assert.ok(ids.includes("qwen/qwen3.6-plus"));
 assert.deepEqual(body.reasoningLevels, ["off", "low", "medium", "high"]);
 assert.equal(body.executionEnabled, false);
 assert.equal(body.publicationAuthorized, false);
 assert.equal(body.rssEnabled, false);
});

test("the owner switches the managing model and the limits", async () => {
 const {storage, store} = fixture();
 const r = await post(storage, {confirm: true, version: 0, model: "anthropic/claude-opus-5.5",
  reasoning: "high", dailyRub: 12, perCallRub: 3});
 assert.equal(r.status, 200);
 const body = await get(storage);
 assert.equal(body.settings.model, "anthropic/claude-opus-5.5");
 assert.equal(body.settings.reasoning, "high");
 assert.equal(body.settings.dailyRub, 12);
 assert.equal(body.settings.perCallRub, 3);
 assert.equal(body.settings.version, 1);
 // The history records what changed.
 assert.equal(body.history.length, 1);
 assert.equal(body.history[0].model, "anthropic/claude-opus-5.5");
 assert.equal(body.history[0].reasoning, "high");
 assert.ok(store.get("agent:settings"));
});

test("the owner may switch back to Qwen", async () => {
 const {storage} = fixture();
 assert.equal((await post(storage, {confirm: true, version: 0, model: "qwen/qwen3.6-plus",
  reasoning: "off", dailyRub: 10, perCallRub: 2})).status, 200);
 const body = await get(storage);
 assert.equal(body.settings.model, "qwen/qwen3.6-plus");
 assert.equal(body.settings.reasoning, "off");
});

test("an unknown model, a bad level and a bad limit are refused", async () => {
 const {storage, store} = fixture();
 const cases = [
  [{confirm: true, version: 0, model: "nobody/nothing", reasoning: "low", dailyRub: 10, perCallRub: 2}, "unknown_agent_model"],
  [{confirm: true, version: 0, model: "anthropic/claude-sonnet-5.5", reasoning: "extreme", dailyRub: 10, perCallRub: 2}, "invalid_agent_reasoning"],
  [{confirm: true, version: 0, model: "anthropic/claude-sonnet-5.5", reasoning: "low", dailyRub: 0, perCallRub: 2}, "invalid_agent_daily_limit"],
  [{confirm: true, version: 0, model: "anthropic/claude-sonnet-5.5", reasoning: "low", dailyRub: 10, perCallRub: 0}, "invalid_agent_call_limit"],
  [{confirm: true, version: 0, model: "anthropic/claude-sonnet-5.5", reasoning: "low", dailyRub: 10, perCallRub: 20}, "invalid_agent_limits"],
 ];
 for(const [body, error] of cases) {
  const r = await post(storage, body);
  assert.equal(r.status, 400, error);
  assert.equal((await r.json()).error, error);
 }
 // Nothing was written and no confirmation is required to be present.
 assert.equal(store.get("agent:settings"), undefined);
 assert.equal((await post(storage, {version: 0, model: "anthropic/claude-sonnet-5.5", reasoning: "low", dailyRub: 10, perCallRub: 2})).status, 400);
});

test("a stale version never overwrites a newer setting", async () => {
 const {storage} = fixture();
 assert.equal((await post(storage, {confirm: true, version: 0, model: "anthropic/claude-opus-5.5",
  reasoning: "low", dailyRub: 10, perCallRub: 2})).status, 200);
 const stale = await post(storage, {confirm: true, version: 0, model: "qwen/qwen3.6-plus",
  reasoning: "off", dailyRub: 10, perCallRub: 2});
 assert.equal(stale.status, 409);
 assert.equal((await stale.json()).error, "stale_agent_settings");
 const body = await get(storage);
 assert.equal(body.settings.model, "anthropic/claude-opus-5.5");
});

test("an unsettled paid request blocks a settings change", async () => {
 const {storage, store} = fixture();
 await store.set("agent:charge-hold", {id: "agent:1", status: "pending"});
 const r = await post(storage, {confirm: true, version: 0, model: "anthropic/claude-opus-5.5",
  reasoning: "low", dailyRub: 10, perCallRub: 2});
 assert.equal(r.status, 423);
 assert.equal((await r.json()).error, "charge_reconciliation_required");
});


test("a production hold preserves production accounting while the owner saves chat settings", async () => {
 const {storage, store} = fixture();
 const hold = {id: "agent:production", operation: "agent:production/science/1/plan"};
 await storage.put("account:production-hold", hold);
 await storage.put("budget:executor-report", {budget: {unsettledRequest: true}});
 const r = await post(storage, {...initialAgentSettings(), reasoning: "high", dailyRub: 25, perCallRub: 4, confirm: true});
 assert.equal(r.status, 200);
 const {settings} = await get(storage);
 assert.equal(settings.reasoning, "high");
 assert.equal(settings.dailyRub, 25);
 assert.equal(settings.perCallRub, 4);
 assert.deepEqual(store.get("account:production-hold"), hold);
 assert.equal(store.get("budget:executor-report").budget.unsettledRequest, true);
});
