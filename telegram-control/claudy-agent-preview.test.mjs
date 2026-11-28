import {test} from "node:test";
import assert from "node:assert/strict";
import {claudyPayload} from "./claudy-adapter.mjs";
import {validateClaudyAgentReply} from "./claudy-agent-preview.mjs";
const model="anthropic/claude-sonnet-5.5";
const payload=claudyPayload(model,[{role:"system",content:"Return JSON."},{role:"user",content:"Synthetic."}]);
const reply=(data,stop="end_turn")=>({model:payload.model,stop_reason:stop,usage:{input_tokens:42,output_tokens:100},content:[{type:"text",text:JSON.stringify(data)}]});
test("native text and a valid job are previewed without authority",()=>{
 const result=validateClaudyAgentReply(model,payload,reply({reply:"Предлагаю подготовить статью.",proposal:null,job:{items:[{channel:"science",count:1}],day:null,instructions:"Synthetic"}}));
 assert.equal(result.accepted,true);assert.equal(result.parsed.job.items[0].channel,"science");assert.equal(result.costVerified,false);assert.equal(result.executionAuthorized,false);
});
test("invalid channels, excess counts and truncated replies are rejected",()=>{
 for(const items of [[{channel:"unknown",count:1}],[{channel:"science",count:10}]]){
  assert.equal(validateClaudyAgentReply(model,payload,reply({reply:"test",proposal:null,job:{items,day:null,instructions:"Synthetic"}})).accepted,false);
 }
 assert.equal(validateClaudyAgentReply(model,payload,reply({reply:"test",proposal:null},"max_tokens")).accepted,false);
});
