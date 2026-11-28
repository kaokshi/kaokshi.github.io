import {test} from "node:test";
import assert from "node:assert/strict";
import {HTML,CSS,JS} from "./ui.mjs";
test("chat has one instance of its existing controls and its own page",()=>{
 const chat=HTML.match(/<section id="chat"[^>]*>([\s\S]*?)<\/section>/)?.[1];
 assert.ok(chat);
 for(const id of ["agenthistory","agenttext","agentsend","agentreceipt","agentbudget"]){
  assert.equal([...HTML.matchAll(new RegExp('id="'+id+'"','g'))].length,1);
  assert.ok(chat.includes('id="'+id+'"'));
 }
 assert.ok(HTML.includes('data-tab="chat"'));
 assert.ok(JS.includes('if(tab.dataset.tab==="chat"){if(by("developerchat").hidden)loadAgent();else loadDeveloper();}'));
 assert.ok(JS.includes('"/api/core/agent/chat"'));
 assert.ok(JS.includes('consent:true'));
});
test("all navigation destinations exist, including secondary pages",()=>{
 const tabs=[...HTML.matchAll(/data-tab="([^"]+)"/g)].map(m=>m[1]);
 assert.equal(new Set(tabs).size,tabs.length);
 for(const id of tabs)assert.ok(HTML.includes('<section id="'+id+'"'));
 for(const id of ["finance","models","core","settings"]){
  assert.ok(HTML.includes('data-open-tab="'+id+'"'));
  assert.ok(CSS.includes('button[data-tab="'+id+'"]'));
 }
 assert.ok(CSS.includes('repeat(5,minmax(0,1fr))'));
 assert.ok(CSS.includes('env(safe-area-inset-bottom)'));
 assert.ok(JS.includes('link.dataset.openTab'));
});

test("chat history uses single flight and only redraws changed content",()=>{
 assert.ok(JS.includes("if(agentLoadFlight)return agentLoadFlight"));
 assert.ok(JS.includes("if(changed){root.replaceChildren()"));
 assert.ok(JS.includes("await refreshAgentAfterMutation()"));
 assert.ok(JS.includes("Автоматического повтора нет."));
 assert.ok(JS.includes('agentApplicationStates.set(m.id,"saved")'));
});
test("chat shortcuts and accessible navigation preserve the confirmation flow",()=>{
 assert.ok(HTML.indexOf('data-tab="chat"')<HTML.indexOf('data-tab="analytics"'));
 assert.ok(HTML.includes('id="agentcount"'));
 assert.ok(!/id="agenthistory"[^>]*aria-live/.test(HTML));
 assert.ok(JS.includes('if(!by("agentsend").disabled)sendAgent()'));
 assert.ok(/async function sendAgent[\s\S]*?window\.confirm/.test(JS));
 assert.ok(CSS.includes("backdrop-filter:blur(12px)"));
});
test("developer chat has separate controls and no paid submission",()=>{for(const id of ["chatdeveloper","developerchat","devtaskcap","devdaycap","devmonthcap","devbudget","devtext","devcreate","devhistory"])assert.ok(HTML.includes('id="'+id+'"'));assert.ok(JS.includes('"/api/core/developer/action"'));assert.ok(HTML.includes("Выполнение и оплата выключены"));});
