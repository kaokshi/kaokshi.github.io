import { chromium } from "playwright";
import assert from "node:assert/strict";
import {HTML,CSS,JS} from "../telegram-control/ui.mjs";
const browser=await chromium.launch({headless:true});
const fixtures={model:"anthropic/claude-sonnet-5.5",reasoning:"medium",perCall:5,limit:50,chargedOrReserved:0,calls:0,enabled:true,messages:[{id:"synthetic",text:"Тест истории",reply:"Тестовый ответ",status:"complete",cost:0}]};
try{
 for(const width of [320,375,430,1280]){
  const page=await browser.newPage({viewport:{width,height:800},reducedMotion:"reduce"});
  const errors=[],requests=[];let release;
  page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/*",async route=>{
   const url=new URL(route.request().url()),p=url.pathname;
   if(url.origin==="https://telegram.org")return route.fulfill({contentType:"application/javascript",body:'window.Telegram={WebApp:{initData:"synthetic-offline",ready(){},expand(){},colorScheme:"dark"}};'});
   if(url.origin!=="https://zen-ui.test")return route.abort();
   if(p==="/")return route.fulfill({contentType:"text/html",body:HTML});
   if(p==="/app.css")return route.fulfill({contentType:"text/css",body:CSS});
   if(p==="/app.js")return route.fulfill({contentType:"application/javascript",body:JS});
   requests.push(p);
   if(p==="/api/dashboard")return route.fulfill({json:{balance:45.41,checkedAt:Date.now()}});
   if(p==="/api/core/developer")return route.fulfill({json:{policy:{version:0,perTaskKop:1000,dailyKop:5000,monthlyKop:150000},tasks:[{id:"synthetic",text:"Проверить проект",reasoning:"high",limitKop:500,status:"queued_blocked",chargedRub:null}],executionEnabled:false,stopped:false}});
   if(p==="/api/core/agent"){
    if(release)await new Promise(r=>setTimeout(r,180));
    return route.fulfill({json:fixtures});
   }
   // No actual requests to the provider or production endpoints are possible.
   return route.fulfill({status:503,json:{error:"offline_fixture"}});
  });
  await page.goto("https://zen-ui.test/");
  await page.locator("#auth").filter({hasText:"Владелец"}).waitFor();
  const visible=await page.locator("nav [data-tab]:visible").evaluateAll(xs=>xs.map(x=>x.dataset.tab));
  if(width<750)assert.deepEqual(visible,["home","articles","chat","analytics","more"]);
  assert.equal(requests.filter(x=>x==="/api/core/agent").length,0);
  release=true;
  const start=Date.now();
  await page.locator('[data-tab="chat"]').click();
  await page.locator('[data-tab="chat"]').click();
  await page.locator("#agenthistory").getByText("Тестовый ответ").waitFor();
  assert.equal(requests.filter(x=>x==="/api/core/agent").length,1);
  const elapsed=Date.now()-start;
  await page.locator("#agenttext").fill("Привет");
  assert.equal(await page.locator("#agentcount").textContent(),"6 / 1200");
  page.once("dialog",d=>d.dismiss());
  await page.locator("#agenttext").press("Control+Enter");
  assert.equal(requests.filter(x=>x==="/api/core/agent/chat").length,0);
  for(const theme of ["dark","light"]){
   await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"chat horizontal overflow at "+width+" "+theme);
  }
  if(width<750){
   await page.setViewportSize({width,height:520});
   await page.waitForFunction(()=>{const e=document.querySelector("#managerchat .conversation-composer");return e.getBoundingClientRect().bottom<=innerHeight+1});
   await page.setViewportSize({width,height:800});
  }
  await page.locator("#managerchat .chat-options summary").click();
  assert.ok(await page.locator("#managerchat .chat-settings-popover").isVisible());
  await page.locator("#managerchat .chat-options summary").click();
  assert.equal(await page.locator("#managerchat .chat-options").getAttribute("open"),null);
  assert.ok(await page.locator("#agenthistory").getByRole("button",{name:"Копировать"}).isVisible());
  await page.locator("#chatdeveloper").click();
  await page.locator("#devhistory").getByText("Проверить проект").waitFor();
  assert.equal(await page.locator("#developerchat .chat-options").getAttribute("open"),null);
  assert.equal(await page.locator("#managerchat").isVisible(),false);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"developer overflow at "+width);
  await page.locator("#devtext").fill("Тестовый черновик");
  await page.locator("#chatmanager").click();
  assert.equal(await page.locator("#agenttext").inputValue(),"Привет");
  await page.locator("#chatdeveloper").click();
  assert.equal(await page.locator("#devtext").inputValue(),"Тестовый черновик");
  await page.locator("#chatmanager").click();
  assert.equal(requests.filter(x=>x==="/api/core/developer/action").length,0);
  if(width<750){
   await page.locator('[data-tab="more"]').click();
   for(const id of ["finance","models","core","settings"]){
    await page.locator('#more [data-open-tab="'+id+'"]').click();
    assert.equal(await page.locator("#"+id).isVisible(),true);
    assert.equal(await page.locator('[data-tab="more"]').getAttribute("aria-current"),"page");
    assert.equal(await page.evaluate(()=>document.activeElement?.tagName),"H1");
    await page.locator('[data-tab="more"]').click();
   }
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,visible,syntheticHistoryLoadMs:elapsed,paidPosts:0,errors}));
  await page.close();
 }
}finally{await browser.close();}
