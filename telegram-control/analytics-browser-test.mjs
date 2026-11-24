import {chromium} from "playwright";
import {HTML,CSS,JS} from "./ui.mjs";
import assert from "node:assert/strict";
import {createServer} from "node:http";
const server=createServer((req,res)=>{
 const path=new URL(req.url,"http://localhost").pathname;
 if(path==="/app.css"){res.setHeader("Content-Type","text/css");res.end(CSS);}
 else if(path==="/app.js"){res.setHeader("Content-Type","text/javascript");res.end(JS);}
 else if(path==="/app"){res.setHeader("Content-Type","text/html; charset=utf-8");res.end(HTML);}
 else{res.statusCode=404;res.end();}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
const browser=await chromium.launch({headless:true});
try{
 for(const viewport of [{width:390,height:844},{width:1440,height:900},{width:320,height:700}]){
  const page=await browser.newPage({viewport});
  await page.route("https://telegram.org/**",route=>route.fulfill({contentType:"application/javascript",body:"window.Telegram={WebApp:{initData:'synthetic-browser-test',ready(){},expand(){}}};"}));
  await page.route("**/api/**",async route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==="/api/analytics/metrika"){
    const sample={date:"2026-11-24",timezone:"Europe/Moscow",visits:10,users:8,pageviews:15,bounceRate:10,depth:1.5,avgVisitSeconds:30,hourly:Array.from({length:24},(_,hour)=>({hour,visits:hour%3,pageviews:hour%4}))};
    return route.fulfill({json:{collectedAt:"2026-11-24T20:00:00Z",stale:false,automaticSync:false,reports:["cinema","science","legends"].map((channel,i)=>({channel,counterId:[114654979,114655465,114655507][i],status:"binding_unverified",periods:{},intraday:sample}))}});
   }
   return route.fulfill({status:503,json:{error:"synthetic_offline"}});
  });
  await page.goto("http://127.0.0.1:"+server.address().port+"/app");
  await page.locator('[data-tab="statistics"]').click();
  await page.locator("#metrikaperiod").selectOption("today");
  await page.locator("#metrikabody .stats-metrics").waitFor({timeout:10000});
  assert.equal(await page.locator("#metrikabody .stats-metrics article").count(),6);
  assert.equal(await page.locator("#metrikabody .hourly-chart progress").count(),24);
  await page.locator("#statschannel").selectOption("cinema");
  assert.match(await page.locator("#metrikabody").innerText(),/114654979/);
  const layout=await page.evaluate(()=>({width:document.documentElement.scrollWidth,viewport:innerWidth,nav:getComputedStyle(document.querySelector("nav")).gridTemplateColumns,background:getComputedStyle(document.querySelector("#metrikabody .analytics-hero")).backgroundImage}));
  assert.ok(layout.width<=layout.viewport+1,JSON.stringify(layout));
  assert.match(layout.background,/gradient/);
  await page.screenshot({path:"analytics-"+viewport.width+".png",fullPage:true});
  console.log("ANALYTICS_VIEWPORT_OK="+viewport.width);
  await page.close();
 }
}finally{await browser.close();server.close();}
