import {test} from "node:test";
import assert from "node:assert/strict";
import {claudyBalance,claudyMonthlyLimit,claudyFinancialFields,claudyRateLimits,balanceMovement,readClaudyStatus} from "./claudy-monitor.mjs";
test("only explicitly ruble-denominated balances accepted",()=>{
 assert.equal(claudyBalance({data:{balance_rub:"49.59"}}),49.59);
 assert.equal(claudyBalance({currency:"RUB",balance:0}),0);
 for(const v of [{balance:100},{currency:"USD",balance:100},{balance_rub:null},{balance_rub:false},{balance_rub:" "},{balance_rub:-1},{balance_rub:"NaN"}])assert.equal(claudyBalance(v),null);
});
test("missing live headers remain unknown rather than default quota",()=>{
 const missing=claudyRateLimits(new Headers(),123);
 assert.equal(missing.remaining,null);
 assert.equal(missing.limitPerMinute,null);
 assert.equal(missing.documentedDefaultPerMinute,30);
 assert.deepEqual(claudyRateLimits(new Headers({"X-RateLimit-Limit":"30","X-RateLimit-Remaining":"28","X-RateLimit-Reset":"19","Retry-After":"4"}),123),{observedAt:123,limitPerMinute:30,remaining:28,resetSeconds:19,retryAfterSeconds:4,documentedDefaultPerMinute:30,documentedDefaultConcurrent:5});
});
test("balance movement is not confirmed spend",()=>{
 assert.equal(balanceMovement({status:"ok",balanceRub:50,observedAt:1},{status:"ok",balanceRub:49.59,observedAt:2}).netChangeRub,-0.41);
 assert.equal(balanceMovement({status:"ok",balanceRub:50,observedAt:1},{status:"ok",balanceRub:60,observedAt:2}).classification,"net_balance_change_not_confirmed_spend");
 assert.equal(balanceMovement({status:"unavailable"},{status:"ok",balanceRub:0,observedAt:2}),null);
});
test("monitor only reads account endpoint and never exposes raw response or secret",async()=>{
 let calls=0;
 const v=await readClaudyStatus("fake-key",{now:123,fetcher:async(url,opts)=>{
  calls++;assert.equal(url,"https://api.claudy.shop/v1/me");assert.equal(opts.method,"GET");assert.equal(opts.redirect,"error");
  return Response.json({balance_rub:12.3,account:{email:"private@example.com"}},{headers:{"X-RateLimit-Remaining":"20"}});
 }});
 assert.equal(calls,1);assert.equal(v.status,"ok");assert.equal(v.balanceRub,12.3);assert.equal(v.rateLimits.remaining,20);assert.equal(JSON.stringify(v).includes("private@example.com"),false);assert.equal(JSON.stringify(v).includes("fake-key"),false);
 assert.equal((await readClaudyStatus("",{fetcher:()=>{throw Error("unexpected call")}})).status,"not_configured");
 const blocked=await readClaudyStatus("fake",{fetcher:async()=>new Response("",{status:429,headers:{"Retry-After":"6"}})});
 assert.equal(blocked.status,"rate_limited");assert.equal(blocked.rateLimits.retryAfterSeconds,6);assert.equal(blocked.balanceRub,null);
});

test("support-confirmed key and account monthly counters remain distinct",()=>{
 const key={monthly_limit:1500,spent_this_month:"5.59",left:"1494.41"};
 const account_limit={monthly_limit:7500,spent_this_month:7,left:7493};
 assert.deepEqual(claudyFinancialFields({key,account_limit}),{keyLimit:{limitRub:1500,spentRub:5.59,leftRub:1494.41,consistent:true,scope:"monthly_provider_report"},accountLimit:{limitRub:7500,spentRub:7,leftRub:7493,consistent:true,scope:"monthly_provider_report"}});
 assert.equal(claudyMonthlyLimit({monthly_limit:1500,spent_this_month:5}),null);
 assert.equal(claudyMonthlyLimit({...key,left:100}).consistent,false);
 assert.deepEqual(claudyFinancialFields({}),{keyLimit:null,accountLimit:null});
});
test("monthly data from authenticated GET is sanitized",async()=>{
 const d=await readClaudyStatus("fake",{fetcher:async()=>Response.json({balance_rub:45.41,key:{monthly_limit:1500,spent_this_month:0,left:1500,secret:"do-not-expose"}})});
 assert.equal(d.keyLimit.leftRub,1500);
 assert.equal(JSON.stringify(d).includes("do-not-expose"),false);
 assert.equal(d.accountLimit,null);
});
