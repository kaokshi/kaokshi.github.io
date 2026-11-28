import {test} from "node:test";
import assert from "node:assert/strict";
import {chatBilling,compareChatBalances,claimProduction,closeProduction} from "./account.mjs";
import {accountView,bridgeRequest} from "./intake.mjs";
const now=1795536000000;
const op={id:"agent:fixture",operation:"agent:fixture/science/1/plan"};
function fixture(){
 const m=new Map();let tail=Promise.resolve();
 const s={get:async k=>m.get(k),put:async(k,v)=>m.set(k,v),delete:async k=>m.delete(k),transaction:f=>{const p=tail.then(()=>f(s));tail=p.catch(()=>{});return p;}};
 return s;
}
test("shared hold serializes claims and never expires or reopens a closed operation",async()=>{
 const s=fixture(),claim=d=>s.transaction(tx=>claimProduction(tx,d,now));
 const [a,b]=await Promise.all([claim(op),claim(op)]);assert.deepEqual([a.status,b.status],[200,409]);
 assert.equal((await s.transaction(tx=>claimProduction(tx,{...op,operation:op.id+"/science/1/edit"},now+86400000))).status,409);
 assert.equal((await closeProduction(s,{...op,operation:op.id+"/science/2/plan",outcome:"settled"},now)).status,409);
 assert.equal((await closeProduction(s,{...op,outcome:"settled"},now)).status,200);
 assert.equal((await closeProduction(s,{...op,outcome:"settled"},now)).body.replay,true);
 assert.equal((await closeProduction(s,{...op,outcome:"not_sent"},now)).status,409);
 assert.equal((await claim(op)).status,409);
});
test("pending or unknown chat prevents production claim",async()=>{
 const s=fixture();
 for(const status of ["pending","unknown"]){await s.put("agent:charge-hold",{status});assert.equal((await claimProduction(s,op,now)).status,409);}
});
test("authenticated production claim is still disabled and unauthenticated bridge fails closed",async()=>{
 const s=fixture(),job={id:op.id,plan:{items:[{channel:"science",count:1}],day:null,instructions:"Synthetic fixture"},preview:{executorMode:"manual"},status:"blocked_prelaunch",executed:false,publicationAuthorized:false};
 await s.put("production:job:"+op.id,job);
 assert.equal((await accountView(s,{...op,action:"claim"},now)).status,409);
 assert.equal(await s.get("account:production-hold"),undefined);
 const r=await bridgeRequest(new Request("https://example.invalid/internal/zen-core/account",{method:"POST",body:JSON.stringify({...op,action:"claim"})}),{ZEN_CORE_INTAKE_SECRET:"a".repeat(64)});
 assert.equal(r.status,401);
});

test("account status is read-only and omits private chat contents",async()=>{
 const s=fixture();
 await s.put("account:production-hold",{...op,startedAt:now});
 await s.put("agent:charge-hold",{id:"fixture-chat",startedAt:now,status:"unknown",text:"private fixture"});
 const r=await accountView(s,{action:"status"},now);
 assert.equal(r.body.schema,"zen-core/account-status-v1");assert.equal(r.body.production.operation,op.operation);
 assert.equal(r.body.chat.status,"unknown");assert.equal(Object.hasOwn(r.body.chat,"text"),false);
 assert.equal(r.body.executionEnabled,false);
 assert.equal((await s.get("agent:charge-hold")).status,"unknown");
});

test("private execution authorization permits a matching claim only outside emergency stop",async()=>{
 const s=fixture(),job={id:op.id,plan:{items:[{channel:"science",count:1}],day:null,instructions:"Synthetic fixture"},preview:{executorMode:"manual"},status:"blocked_prelaunch",executed:false,publicationAuthorized:false};
 await s.put("production:job:"+op.id,job);
 assert.equal((await accountView(s,{...op,action:"claim"},now,true)).status,200);
 assert.equal((await accountView(s,{...op,action:"close",outcome:"settled"},now,true)).status,200);
 await s.put("ops:state",{requestedMode:"manual",stopped:true,version:1,modeRevision:0});
 assert.equal((await accountView(s,{...op,operation:op.id+"/science/1/draft",action:"claim"},now,true)).status,409);
});

test("bounded uncertain closure releases the lane but permanently closes the operation key", async () => {
 const s=fixture();
 assert.equal((await claimProduction(s,op,now)).status,200);
 assert.equal((await closeProduction(s,{...op,outcome:"bounded_uncertain"},now)).status,200);
 assert.equal((await closeProduction(s,{...op,outcome:"bounded_uncertain"},now)).body.replay,true);
 assert.equal((await claimProduction(s,op,now)).body.error,"account_operation_seen");
 assert.equal((await claimProduction(s,{...op,operation:op.id+"/science/2/plan"},now)).status,200);
});

test("a dedicated chat charge does not block the independent production account",async()=>{
 const s=fixture();
 await s.put("agent:charge-hold",{status:"unknown",billingLane:"verified_dedicated_chat"});
 assert.equal((await claimProduction(s,op,now)).status,200);
});

test("an unverified chat credential cannot bypass a shared account hold",async()=>{
 const env={ROUTERAI_API_KEY:"production",ROUTERAI_CHAT_API_KEY:"chat"};
 assert.deepEqual(chatBilling(env),{token:"production",independent:false});
 assert.equal(compareChatBalances("2619.35","2619.35","2619.35"),"same");
 assert.equal(compareChatBalances("2619.35",null,"2619.35"),"unavailable");
 assert.equal(compareChatBalances("2619.35","0.99","2619.34"),"production_changed");
 assert.equal(compareChatBalances("2619.35","0.99","2619.35"),"distinct");
 const s=fixture();
 await s.put("agent:charge-hold",{status:"unknown",billingLane:"dedicated_chat"});
 assert.equal((await claimProduction(s,op,now)).status,409);
});
