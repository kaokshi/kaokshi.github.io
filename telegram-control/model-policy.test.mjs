import {test} from "node:test";
import assert from "node:assert/strict";
import {DEFAULT_MODELS,DEFAULT_REASONING_POLICY,ROLES,REASONING_LEVELS,policyHash,validateModels,validateReasoning,defaultCatalogue} from "./model-settings.mjs";
import {readModels,writeModels,acknowledgeExecutor} from "./model-settings.mjs";
import {pinFor,effectiveSettingsIn} from "./settings-sync.mjs";

const now = Date.parse("2026-11-27T09:00:00Z");
function fixture() {
 const store = new Map();
 const storage = {get: async k => store.get(k), put: async (k, v) => store.set(k, v), delete: async k => store.delete(k),
  list: async ({prefix}) => new Map([...store].filter(([k]) => k.startsWith(prefix))),
  transaction: async fn => fn(storage)};
 return {store, storage};
}
const verified = models => models.map(id => {
 const image = id === "bytedance-seed/seedream-5-0-flash";
 return {id, capability: image ? "image" : "text", label: id, available: true, priceVerified: true,
  priceSource: "routerai:/models",
  pricing: image ? {image_output: {value: "0.02", unit: "image"}} : {prompt: {value: "0.00001", unit: "token"}, completion: {value: "0.00004", unit: "token"}},
  observedAt: now};
});

test("the catalogue offers the Claude family and the existing models", () => {
 const catalogue = defaultCatalogue();
 const ids = catalogue.map(m => m.id);
 for(const id of ["anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5",
  "qwen/qwen3.6-plus", "deepseek/deepseek-v4-pro-0813", "bytedance-seed/seedream-5-0-flash"]) {
  assert.ok(ids.includes(id), "the catalogue must offer " + id);
 }
 const opus = catalogue.find(m => m.id === "anthropic/claude-opus-5.5");
 assert.equal(opus.capability, "text");
 assert.equal(opus.available, true);
 assert.equal(opus.priceVerified, false, "a price is only known after the live snapshot");
});

test("every role lists the text candidates and the image role only the image model", () => {
 const plan = ROLES.find(r => r.id === "plan");
 assert.ok(plan.candidates.includes("anthropic/claude-opus-5.5"));
 assert.ok(plan.candidates.includes("anthropic/claude-haiku-5.5"));
 assert.ok(plan.candidates.includes("qwen/qwen3.6-plus"));
 const images = ROLES.find(r => r.id === "images");
 assert.deepEqual(images.candidates, ["bytedance-seed/seedream-5-0-flash"]);
});

test("the owner may pick a different Claude model for a text role", () => {
 const catalogue = verified([...ROLES.map(r => r.defaultModel), "anthropic/claude-opus-5.5"]);
 const target = {...DEFAULT_MODELS, plan: "anthropic/claude-opus-5.5"};
 const v = validateModels(target, catalogue);
 assert.equal(v.ok, true, JSON.stringify(v));
 assert.equal(v.models.plan, "anthropic/claude-opus-5.5");
 // The image role may not take a text model and the editor stays independent.
 const wrong = validateModels({...target, images: "anthropic/claude-opus-5.5"}, catalogue);
 assert.equal(wrong.ok, false);
 assert.equal(wrong.error, "wrong_capability");
 const same = validateModels({...target, edit: target.draft}, catalogue);
 assert.equal(same.ok, false);
 assert.equal(same.error, "editor_not_independent");
});

test("a model that is not a candidate for the role is refused", () => {
 const catalogue = verified([...ROLES.map(r => r.defaultModel), "anthropic/claude-opus-5.5"]);
 const v = validateModels({...DEFAULT_MODELS, images: "anthropic/claude-opus-5.5"}, catalogue);
 assert.equal(v.ok, false);
 assert.equal(v.error, "wrong_capability");
});

test("the reasoning level is validated per role", () => {
 assert.equal(validateReasoning(DEFAULT_REASONING_POLICY).ok, true);
 for(const level of REASONING_LEVELS) {
  const r = {...DEFAULT_REASONING_POLICY, plan: level};
  const v = validateReasoning(r);
  assert.equal(v.ok, true, "level " + level);
 }
 assert.equal(validateReasoning({...DEFAULT_REASONING_POLICY, plan: "extreme"}).ok, false);
 // The image role has no thinking level.
 assert.equal(validateReasoning({...DEFAULT_REASONING_POLICY, images: "high"}).ok, false);
 assert.equal(validateReasoning({...DEFAULT_REASONING_POLICY, images: "high"}).error, "reasoning_not_supported");
});

test("the policy hash covers the model and the reasoning level", async () => {
 const base = await policyHash(DEFAULT_MODELS, DEFAULT_REASONING_POLICY);
 const other = await policyHash(DEFAULT_MODELS, {...DEFAULT_REASONING_POLICY, plan: "high"});
 assert.notEqual(base, other, "the thinking level must change the revision");
 assert.equal(base.length, 64);
});

test("a written revision keeps the model and the thinking level together", async () => {
 const {storage, store} = fixture();
 const catalogue = verified([...ROLES.map(r => r.defaultModel), "anthropic/claude-opus-5.5"]);
 const target = {...DEFAULT_MODELS, plan: "anthropic/claude-opus-5.5"};
 const reasoning = {...DEFAULT_REASONING_POLICY, plan: "high", draft: "medium"};
 const r = await writeModels(storage, {confirm: true, revision: 0, action: "set", models: target, reasoning}, now, catalogue);
 assert.equal(r.status, 200);
 const body = r.body;
 assert.equal(body.requested.revision, 1);
 assert.equal(body.requested.models.plan, "anthropic/claude-opus-5.5");
 assert.equal(body.requested.reasoning.plan, "high");
 assert.equal(body.requested.reasoning.draft, "medium");
 assert.equal(body.requested.hash, await policyHash(target, reasoning));
 const stored = store.get("models:policy");
 assert.equal(stored.reasoning.plan, "high");
});

test("an invalid thinking level never reaches the policy", async () => {
 const {storage, store} = fixture();
 const r = await writeModels(storage, {confirm: true, revision: 0, action: "set", models: {...DEFAULT_MODELS}, reasoning: {...DEFAULT_REASONING_POLICY, plan: "extreme"}}, now);
 assert.equal(r.status, 400);
 assert.equal(r.body.error, "invalid_reasoning");
 assert.equal(store.get("models:policy"), undefined);
});

test("an order pins the model revision and the thinking level together", async () => {
 const {storage, store} = fixture();
 const catalogue = verified([...ROLES.map(r => r.defaultModel)]);
 const reasoning = {...DEFAULT_REASONING_POLICY, plan: "high"};
 assert.equal((await writeModels(storage, {confirm: true, revision: 0, action: "set", models: {...DEFAULT_MODELS}, reasoning}, now, catalogue)).status, 200);
 // Until the executor acknowledges the new revision, a new order pins the old one.
 const before = await pinFor(storage);
 assert.equal(before.models.revision, 0);
 assert.equal(before.reasoning.plan, "off");
 // The executor acknowledges the requested revision.
 const ack = await acknowledgeExecutor(storage, {revision: 1, hash: await policyHash(DEFAULT_MODELS, reasoning)}, now);
 assert.equal(ack.ok, true);
 const pin = await pinFor(storage);
 assert.equal(pin.models.revision, 1);
 assert.equal(pin.reasoning.plan, "high");
 assert.equal(pin.reasoning.draft, "low");
});

test("Claudy rates are displayed without changing RouterAI verification or enabling execution",async()=>{
 const {storage,store}=fixture();
 const d=await readModels(storage);
 const sonnet=d.providerPlan.find(m=>m.id==="anthropic/claude-sonnet-5.5");
 assert.deepEqual(sonnet,{id:"anthropic/claude-sonnet-5.5",targetProvider:"claudy",nativeModel:"claude-sonnet-5-5",executionEnabled:false,rateStatus:"configured_estimate",inputRubPerMillion:30,outputRubPerMillion:150,settlementVerified:false});
 assert.equal(d.catalogue.find(m=>m.id===sonnet.id).priceVerified,false);
 assert.equal(d.providerPlan.find(m=>m.id==="deepseek/deepseek-v4-pro-0813").targetProvider,"routerai");
 assert.equal(d.providerPlan.find(m=>m.id==="bytedance-seed/seedream-5-0-flash").targetProvider,"routerai");
 assert.equal(d.effectiveTransport,"routerai");assert.equal(d.claudyExecutionEnabled,false);
 assert.equal(store.size,0);
 assert.equal(validateModels({...DEFAULT_MODELS,plan:"anthropic/claude-opus-5.5"},d.catalogue).error,"unknown_price");
});
