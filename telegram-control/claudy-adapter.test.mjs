import {test} from "node:test";
import assert from "node:assert/strict";
import {claudyModel,claudyPayload,claudyBound,claudyHeaders,claudyResponse} from "./claudy-adapter.mjs";
const model="anthropic/claude-sonnet-5.5";
const msgs=[{role:"system",content:"Return JSON."},{role:"user",content:"Synthetic request."}];
test("native Claude requests retain logical model mapping and owner effort",()=>{
 const p=claudyPayload(model,msgs,"medium");
 assert.equal(p.model,"claude-sonnet-5-5");
 assert.equal(p.system,msgs[0].content);
 assert.deepEqual(p.thinking,{type:"adaptive"});
 assert.deepEqual(p.output_config,{effort:"medium"});
 assert.equal(p.max_tokens,8000);
 assert.equal(claudyHeaders("fake")["X-Claudy-Cache"],"off");
 assert.equal(claudyModel("deepseek/deepseek-v4-pro-0813"),null);
 assert.throws(()=>claudyPayload("unknown",msgs),/configuration_invalid/);
 assert.throws(()=>claudyPayload(model,msgs,"medium",32001),/configuration_invalid/);
});
test("request estimate remains distinct from verified charges",()=>{
 const p=claudyPayload(model,msgs);
 const b=claudyBound(model,p,5);
 assert.equal(b.withinLimit,true);
 assert.equal(b.estimateOnly,true);
 assert.equal(claudyBound(model,p,0.01).withinLimit,false);
 const r=claudyResponse(model,p,{model:p.model,stop_reason:"end_turn",usage:{input_tokens:100,output_tokens:200,cache_creation_input_tokens:50},content:[{type:"thinking",thinking:"private"},{type:"text",text:"hello"}]});
 assert.equal(r.reply,"hello");
 assert.equal(r.complete,true);
 assert.equal(r.costVerified,false);
 assert.ok(Math.abs(r.estimatedRub-0.036)<1e-9);
 assert.equal(JSON.stringify(r).includes("private"),false);
});
test("invalid model, excessive usage and truncated replies are not accepted as complete",()=>{
 const p=claudyPayload(model,msgs);
 const response={model:p.model,stop_reason:"max_tokens",usage:{input_tokens:20,output_tokens:8000},content:[{type:"text",text:"partial"}]};
 assert.equal(claudyResponse(model,p,response).complete,false);
 assert.throws(()=>claudyResponse(model,p,{...response,model:"wrong"}),/model_mismatch/);
 assert.throws(()=>claudyResponse(model,p,{...response,usage:{input_tokens:20,output_tokens:8001}}),/usage_over_bound/);
 assert.throws(()=>claudyResponse(model,p,{...response,usage:{input_tokens:"20",output_tokens:20}}),/usage_invalid/);
});
