import {test} from "node:test";
import assert from "node:assert/strict";
import {claudyPayload} from "./claudy-adapter.mjs";
import {claudyCountTokens,claudyPreflight,claudyEvidence} from "./claudy-preflight.mjs";
const model="anthropic/claude-sonnet-5.5";
const p=claudyPayload(model,[{role:"system",content:"Return JSON."},{role:"user",content:"Synthetic."}]);
test("count only calls free token count endpoint",async()=>{
 let calls=0;
 const result=await claudyCountTokens("fake",model,p,{fetcher:async(url,opts)=>{
  calls++;assert.equal(url,"https://api.claudy.shop/v1/messages/count_tokens");
  assert.equal(opts.redirect,"error");assert.equal(JSON.parse(opts.body).model,p.model);
  assert.equal("max_tokens" in JSON.parse(opts.body),false);
  return Response.json({input_tokens:42});
 }});
 assert.deepEqual(result,{status:"ok",inputTokens:42});assert.equal(calls,1);
 const bad=await claudyCountTokens("fake",model,p,{fetcher:async()=>Response.json({input_tokens:"42"})});
 assert.equal(bad.status,"invalid_usage");
});
test("unknown or insufficient budget fails closed",()=>{
 assert.equal(claudyPreflight(model,p,{status:"unavailable"},10,5).allowed,false);
 assert.equal(claudyPreflight(model,p,{status:"ok",inputTokens:42},0.01,5).reason,"insufficient_balance");
 assert.equal(claudyPreflight(model,p,{status:"ok",inputTokens:100000},50,5).reason,"call_budget_exceeded");
 const result=claudyPreflight(model,p,{status:"ok",inputTokens:42},50,5);
 assert.equal(result.allowed,false);assert.equal(result.budgetEligible,true);assert.equal(result.reason,"provider_settlement_unavailable");assert.equal(result.estimateOnly,true);assert.equal(result.settlementAvailable,false);
});
test("request identifiers are diagnostic only",()=>{
 const x=claudyEvidence(new Headers({"request-id":"req_123","X-Claudy-Cache":"bypass"}),{id:"msg_123",cost:0.01});
 assert.deepEqual(x,{provider:"claudy",requestId:"req_123",messageId:"msg_123",cacheMode:"bypass",costVerified:false,settlementSource:null});
 assert.equal(claudyEvidence(new Headers(),{id:"unsafe id"}).messageId,null);
});
