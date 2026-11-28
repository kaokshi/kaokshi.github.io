import {test} from "node:test";
import assert from "node:assert/strict";
import {AgentHandler,estimate,MODEL} from "./agent.mjs";
import {CoreHandler} from "./core.mjs";
import {writeMemory} from "./memory.mjs";
import {writeAgentSettings} from "./agent-settings.mjs";

const now = Date.now();
const priceRow = id => ({id, pricing_units: {prompt: "token", completion: "token"}, pricing: {prompt: "0.00001", completion: "0.00004"}});
function fixture() {
 const store = new Map();
 let alarm = null;
 const storage = {get: async k => store.get(k), put: async (k, v) => store.set(k, v), delete: async k => store.delete(k),
  list: async ({prefix}) => new Map([...store].filter(([k]) => k.startsWith(prefix))),
  transaction: async fn => fn(storage),
  getAlarm: async () => alarm, setAlarm: async n => {alarm = n;}, deleteAlarm: async () => {alarm = null;}};
 return {store, storage};
}
const plan = {timezone: "Europe/Moscow", paused: true, leadMinutes: 360,
 channels: [{channel: "cinema", enabled: false, times: []}, {channel: "legends", enabled: true, times: ["12:00"]}, {channel: "science", enabled: false, times: []}]};

test("the chat sends the configured model and the configured thinking level", async () => {
 const {storage} = fixture();
 const core = new CoreHandler({storage}), agent = new AgentHandler({storage}, {ROUTERAI_API_KEY: "fake"}, core);
 const id = "9b00a000-1111-4222-8333-444444444444";
 let payload = null;
 const original = globalThis.fetch;
 globalThis.fetch = async (url, opts) => {
  if(url.endsWith("/models"))return Response.json({data: [priceRow("anthropic/claude-opus-5.5")]});
  if(url.endsWith("/credits"))return Response.json({data: {credits: 100}});
  payload = JSON.parse(opts.body);
  return Response.json({model: "anthropic/claude-opus-5.5", usage: {cost: 0.01},
   choices: [{finish_reason: "stop", message: {content: JSON.stringify({reply: "Готов", proposal: null})}}]});
 };
 try{
  assert.equal((await writeAgentSettings(storage, {confirm: true, version: 0,
   model: "anthropic/claude-opus-5.5", reasoning: "high", dailyRub: 10, perCallRub: 2, opusDailyRub: 1}, now)).status, 200);
  const r = await agent.chat({id, text: "Привет", consent: true});
  assert.equal(r.status, 200);
  assert.equal(r.body.message.status, "complete");
  assert.equal(payload.model, "anthropic/claude-opus-5.5");
  assert.deepEqual(payload.reasoning, {effort: "high"});
  assert.equal(payload.max_tokens, 700);
  const view = await agent.view();
  assert.equal(view.confirmedRub, 0.01);
  assert.ok(Math.abs(view.opusAccountedRub - 0.01) < 1e-9);
  const blocked = await writeAgentSettings(storage, {confirm: true, version: 1,
   model: payload.model, reasoning: "high", dailyRub: 10, perCallRub: 2, opusDailyRub: 0}, now);
  assert.equal(blocked.status, 200);
  const second = await agent.chat({id: "9b00a000-1111-4222-8333-444444444445", text: "Ещё", consent: true});
  assert.equal(second.status, 409);
 }finally{globalThis.fetch = original;}
});

test("a call that does not fit the per-request limit is refused and costs nothing", async () => {
 const {storage} = fixture();
 const core = new CoreHandler({storage}), agent = new AgentHandler({storage}, {ROUTERAI_API_KEY: "fake"}, core);
 const id = "9b00a000-1111-4222-8333-444444444444";
 let paid = 0;
 const original = globalThis.fetch;
 globalThis.fetch = async (url, opts) => {
  if(url.endsWith("/models"))return Response.json({data: [priceRow("anthropic/claude-opus-5.5")]});
  if(url.endsWith("/credits"))return Response.json({data: {credits: 100}});
  paid++;
  return Response.json({model: "anthropic/claude-opus-5.5", usage: {cost: 0.01}, choices: [{finish_reason: "stop", message: {content: "{}"}}]});
 };
 try{
  // A tiny per-request limit cannot cover the call.
  assert.equal((await writeAgentSettings(storage, {confirm: true, version: 0,
   model: "anthropic/claude-opus-5.5", reasoning: "low", dailyRub: 10, perCallRub: 0.000001}, now)).status, 200);
  const r = await agent.chat({id, text: "Привет", consent: true});
  assert.equal(r.status, 409);
  assert.equal(r.body.error, "agent_call_exceeds_limit");
  assert.equal(r.body.suggestion, "raise_per_call_limit");
  assert.equal(paid, 0, "no paid request may be made");
 }finally{globalThis.fetch = original;}
});

test("changing the managing model keeps the memory and the conversation history", async () => {
 const {storage} = fixture();
 const core = new CoreHandler({storage}), agent = new AgentHandler({storage}, {ROUTERAI_API_KEY: "fake"}, core);
 const notes = [{kind: "style", text: "Живой русский язык"}];
 assert.equal((await writeMemory(storage, {notes, version: 0, confirm: true})).status, 200);
 const original = globalThis.fetch;
 let payload = null;
 globalThis.fetch = async (url, opts) => {
  if(url.endsWith("/models"))return Response.json({data: [priceRow("anthropic/claude-sonnet-5.5"), priceRow("qwen/qwen3.6-plus")]});
  if(url.endsWith("/credits"))return Response.json({data: {credits: 100}});
  payload = JSON.parse(opts.body);
  return Response.json({model: payload.model, usage: {cost: 0.01},
   choices: [{finish_reason: "stop", message: {content: JSON.stringify({reply: "Принято", proposal: null})}}]});
 };
 try{
  const first = await agent.chat({id: "9b00a000-1111-4222-8333-444444444444", text: "Первый вопрос", consent: true});
  assert.equal(first.status, 200);
  assert.equal((await agent.view()).messages.length, 1);
  // The owner switches the managing model.
  assert.equal((await writeAgentSettings(storage, {confirm: true, version: 0,
   model: "qwen/qwen3.6-plus", reasoning: "off", dailyRub: 10, perCallRub: 2}, now)).status, 200);
  const second = await agent.chat({id: "9b00a000-1111-4222-8333-444444444445", text: "Второй вопрос", consent: true});
  assert.equal(second.status, 200);
  assert.equal(payload.model, "qwen/qwen3.6-plus");
  // The memory and the earlier turn are still there.
  const sent = JSON.parse(payload.messages[1].content);
  assert.deepEqual(sent.editorialMemory, notes);
  assert.equal(sent.history.length, 1);
  assert.equal(sent.history[0].user, "Первый вопрос");
  assert.equal((await agent.view()).messages.length, 2);
 }finally{globalThis.fetch = original;}
});

test("the chat view reports the configured model and the actual spend", async () => {
 const {storage} = fixture();
 const core = new CoreHandler({storage}), agent = new AgentHandler({storage}, {ROUTERAI_API_KEY: "fake"}, core);
 assert.equal((await writeAgentSettings(storage, {confirm: true, version: 0,
  model: "anthropic/claude-haiku-5.5", reasoning: "low", dailyRub: 15, perCallRub: 4}, now)).status, 200);
 const view = await agent.view();
 assert.equal(view.model, "anthropic/claude-haiku-5.5");
 assert.equal(view.reasoning, "low");
 assert.equal(view.limit, 15);
 assert.equal(view.perCall, 4);
 assert.equal(view.chargedOrReserved, 0);
 assert.equal(view.calls, 0);
 assert.equal(view.mode, "owner_chat_only");
});

test("dedicated chat uses its own key while the production hold remains closed", async () => {
 const {storage} = fixture();
 await storage.put("account:production-hold", {id:"production", operation:"production/science/1/plan"});
 const agent = new AgentHandler({storage}, {ROUTERAI_API_KEY:"production-key", ROUTERAI_CHAT_API_KEY:"chat-key", ZEN_CHAT_BALANCE_VERIFIED:"true"}, new CoreHandler({storage}));
 const original = globalThis.fetch;
 let paid = 0;
 globalThis.fetch = async (url, opts) => {
  assert.equal(opts.headers.Authorization, "Bearer chat-key");
  if(url.endsWith("/models"))return Response.json({data:[priceRow(MODEL)]});
  if(url.endsWith("/credits"))return Response.json({data:{credits:10}});
  paid++;
  return Response.json({model:MODEL,usage:{cost:0.01},
   choices:[{finish_reason:"stop",message:{content:'{"reply":"Synthetic response","proposal":null}'}}]});
 };
 try{
  assert.equal((await agent.view()).enabled,true);
  const r=await agent.chat({id:"9b00a000-1111-4222-8333-444444444444",text:"Synthetic",consent:true});
  assert.equal(r.body.message.costVerified,true);
  assert.equal(paid,1);
  assert.equal((await storage.get("account:production-hold")).id,"production");
 }finally{globalThis.fetch=original;}
});


test("chat generation receipt survives an interrupted response without retry", async () => {
 const {storage}=fixture();
 const agent=new AgentHandler({storage},{ROUTERAI_API_KEY:"fake"},new CoreHandler({storage}));
 const id="9b00a000-1111-4222-8333-444444444444";
 const original=globalThis.fetch;
 let paid=0;
 globalThis.fetch=async url=>{
  if(url.endsWith("/models"))return Response.json({data:[priceRow(MODEL)]});
  if(url.endsWith("/credits"))return Response.json({data:{credits:100}});
  paid++;
  return {ok:true,headers:new Headers({"X-Generation-Id":"rai-fixture-123"}),json:async()=>{
   assert.equal((await storage.get("agent:generation:"+id)).generationId,"rai-fixture-123");
   throw Error("interrupted");
  }};
 };
 try{
  const result=await agent.chat({id,text:"Synthetic",consent:true});
  assert.equal(result.body.message.status,"failed");
  assert.equal((await storage.get("agent:charge-hold")).generationId,"rai-fixture-123");
  assert.equal((await storage.get("agent:charge-hold")).status,"unknown");
  assert.equal((await storage.get("agent:generation:"+id)).model,MODEL);
  assert.equal((await agent.chat({id,text:"Synthetic",consent:true})).status,409);
  assert.equal(paid,1);
  assert.equal((await agent.view()).enabled,false);
  assert.equal(JSON.stringify(await agent.view()).includes("rai-fixture-123"),false);
 }finally{globalThis.fetch=original;}
});
