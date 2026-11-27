import {test} from "node:test";
import assert from "node:assert/strict";
import {CoreHandler} from "./core.mjs";
import {DEFAULT_MODELS,DEFAULT_REASONING_POLICY,ROLES,policyHash} from "./model-settings.mjs";

const now = Date.parse("2026-11-27T09:00:00Z");
function fixture() {
 const store = new Map();
 const storage = {get: async k => store.get(k), put: async (k, v) => store.set(k, v), delete: async k => store.delete(k),
  list: async ({prefix}) => new Map([...store].filter(([k]) => k.startsWith(prefix))),
  transaction: async fn => fn(storage)};
 return {store, storage};
}
const post = (storage, path, body) => new CoreHandler({storage}).fetch(
 new Request("https://core.internal/core" + path, {method: "POST", body: JSON.stringify(body)}));

test("the model policy is readable and starts on the default revision", async () => {
 const {storage} = fixture();
 const r = await new CoreHandler({storage}).fetch(new Request("https://core.internal/core/models", {method: "GET"}));
 assert.equal(r.status, 200);
 const body = await r.json();
 assert.equal(body.requested.revision, 0);
 assert.equal(body.requested.hash, await policyHash(DEFAULT_MODELS, DEFAULT_REASONING_POLICY));
 assert.deepEqual(body.requested.models, {...DEFAULT_MODELS});
 assert.equal(body.pendingSync, false);
 assert.equal(body.executorSync, "not_connected");
 assert.equal(body.executionEnabled, false);
 assert.equal(body.publicationAuthorized, false);
 assert.equal(body.rssEnabled, false);
 assert.equal(body.roles.length, ROLES.length);
});

test("the owner writes a new model revision that is recorded in history", async () => {
 const {storage, store} = fixture();
 const target = {...DEFAULT_MODELS, draft: "anthropic/claude-sonnet-5.5"};
 const r = await post(storage, "/models/settings", {confirm: true, action: "set", revision: 0, models: target});
 assert.equal(r.status, 200);
 const body = await r.json();
 assert.equal(body.requested.revision, 1);
 assert.equal(body.requested.models.draft, "anthropic/claude-sonnet-5.5");
 assert.equal(body.requested.hash, await policyHash(target, DEFAULT_REASONING_POLICY));
 assert.equal(body.pendingSync, true, "a new revision is not yet acknowledged by the executor");
 assert.equal(body.history.length, 1);
 assert.equal(body.history[0].revision, 1);
 assert.equal(body.history[0].action, "set");
 assert.equal(body.history[0].previousRevision, 0);
 // The immutable history entry and the current policy both exist.
 assert.ok(store.get("models:history:" + String(1).padStart(12, "0")));
 assert.equal(store.get("models:policy").revision, 1);
});

test("a model change needs confirmation, a known revision and a valid catalogue entry", async () => {
 const {storage} = fixture();
 assert.equal((await post(storage, "/models/settings", {action: "set", revision: 0, models: DEFAULT_MODELS})).status, 400);
 assert.equal((await post(storage, "/models/settings", {confirm: true, revision: "0", models: DEFAULT_MODELS})).status, 400);
 assert.equal((await post(storage, "/models/settings", {confirm: true, revision: 0})).status, 400);
 assert.equal((await post(storage, "/models/settings", {confirm: true, revision: 0, action: "nonsense", models: DEFAULT_MODELS})).status, 400);
 // An unknown model is refused.
 const unknown = await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: {...DEFAULT_MODELS, draft: "nobody/nothing"}});
 assert.equal(unknown.status, 400);
 assert.equal((await unknown.json()).error, "unknown_model");
 // The editor must stay independent of the author.
 const same = await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: {...DEFAULT_MODELS, edit: DEFAULT_MODELS.draft}});
 assert.equal(same.status, 400);
 assert.equal((await same.json()).error, "editor_not_independent");
});

test("a stale revision never overwrites a newer policy", async () => {
 const {storage} = fixture();
 const target = {...DEFAULT_MODELS, draft: "anthropic/claude-sonnet-5.5"};
 assert.equal((await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: target})).status, 200);
 const stale = await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: target});
 assert.equal(stale.status, 409);
 assert.equal((await stale.json()).error, "stale_revision");
});

test("a rollback restores an earlier revision and keeps the history", async () => {
 const {storage} = fixture();
 const target = {...DEFAULT_MODELS, draft: "anthropic/claude-sonnet-5.5"};
 assert.equal((await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: target})).status, 200);
 const r = await post(storage, "/models/settings", {confirm: true, revision: 1, action: "rollback", targetRevision: 0});
 assert.equal(r.status, 200);
 const body = await r.json();
 assert.equal(body.requested.revision, 2);
 assert.deepEqual(body.requested.models, {...DEFAULT_MODELS});
 assert.equal(body.history[0].rollbackOf, 0);
 // An unknown rollback target is refused.
 const bad = await post(storage, "/models/settings", {confirm: true, revision: 2, action: "rollback", targetRevision: 99});
 assert.equal(bad.status, 404);
 assert.equal((await bad.json()).error, "unknown_revision");
});

test("a financial hold blocks any model change", async () => {
 const {storage, store} = fixture();
 await store.set("agent:charge-hold", {id: "agent:1", status: "pending"});
 const r = await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: DEFAULT_MODELS});
 assert.equal(r.status, 423);
 assert.equal((await r.json()).error, "financial_hold");
 // And an unsettled paid request blocks it too.
 const {storage: s2, store: st2} = fixture();
 await st2.set("budget:executor-report", {budget: {unsettledRequest: true}});
 const r2 = await post(s2, "/models/settings", {confirm: true, revision: 0, action: "set", models: DEFAULT_MODELS});
 assert.equal(r2.status, 423);
 assert.equal((await r2.json()).error, "in_flight_paid_request");
});

test("an order keeps its own pinned revision after the policy changes", async () => {
 const {storage, store} = fixture();
 const target = {...DEFAULT_MODELS, draft: "anthropic/claude-sonnet-5.5"};
 assert.equal((await post(storage, "/models/settings", {confirm: true, revision: 0, action: "set", models: target})).status, 200);
 const pinned = await import("./settings-sync.mjs");
 // The executor has acknowledged revision 0; a new order must pin that revision,
 // not the requested revision 1 that the executor has not acknowledged yet.
 await store.set("models:executor-ack", {revision: 0, hash: await policyHash(DEFAULT_MODELS, DEFAULT_REASONING_POLICY), acknowledgedAt: new Date(now).toISOString()});
 const pin = await pinned.pinFor(storage);
 assert.equal(pin.models.revision, 0, "a new order pins the revision the executor actually runs");
 assert.equal(pin.models.hash, await policyHash(DEFAULT_MODELS, DEFAULT_REASONING_POLICY));
 const effective = await pinned.effectiveSettingsIn(storage);
 assert.equal(effective.models.revision, 0, "the executor still runs the revision it acknowledged");
 assert.equal(effective.models.source, "executor_ack");
 // The requested policy is ahead of the effective one, so a sync is pending.
 const view = await new CoreHandler({storage}).fetch(new Request("https://core.internal/core/models", {method: "GET"}));
 const body = await view.json();
 assert.equal(body.requested.revision, 1);
 assert.equal(body.effective.revision, 0);
 assert.equal(body.pendingSync, true);
 assert.equal(body.executorSync, "acknowledged");
});
