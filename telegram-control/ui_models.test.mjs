import test from "node:test";
import assert from "node:assert/strict";
import {HTML,JS} from "./ui.mjs";
import {MODELS_JS} from "./ui-models.mjs";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

// Synthetic fixture only: no owner data, no Telegram session, no network.
// The panel is checked through the markup and the loader source; the loader
// itself is exercised against the Core route in model-settings-route.test.mjs.
test("the AI team panel renders the model policy from the core route",()=>{
 assert.match(HTML,/id="modelshint"/);
 assert.match(HTML,/id="modelpanel"/);
 // The static model cards are replaced by the live policy.
 assert.doesNotMatch(HTML,/Целевая связка · не онлайн-проверка моделей/);
 assert.match(HTML,/data-tab="models"/);
});

test("the panel loads the policy and shows the pending sync state",()=>{
 const start=JS.indexOf("async function loadModels()");
 assert.ok(start>0,"the models loader must exist");
 const body=MODELS_JS;
 assert.match(body,/coreRequest\("\/api\/core\/models"\)/);
 assert.match(body,/d\.requested\.revision/);
 assert.match(body,/d\.pendingSync/);
 assert.match(body,/d\.effective\.revision/);
 assert.match(body,/d\.roles/);
 // A failure must not leave a stale or misleading claim on screen.
 assert.match(body,/Часть настроек недоступна/);
});

test("the models tab loads the panel and the theme and Telegram auth stay intact",()=>{
 assert.match(JS,/if\(tab\.dataset\.tab==="models"\)loadModels\(\);/);
 // Existing behaviour must survive the change.
 assert.match(JS,/THEME_KEY="zencontrol.theme"/);
 assert.match(JS,/function resolveTheme\(pref\)/);
 assert.match(JS,/launchData\(\)/);
 assert.match(HTML,/telegram-web-app\.js/);
 assert.match(JS,/function initTheme\(\)/);
});

test("browser bundle compiles and model panel is inside its private page scope",()=>{
 assert.doesNotThrow(()=>new Function(JS));
 assert.ok(JS.startsWith("(()=>{"));
 assert.ok(JS.endsWith("})();"));
 assert.ok(JS.indexOf(MODELS_JS)>0);
 assert.ok(JS.indexOf(MODELS_JS)+MODELS_JS.length<JS.lastIndexOf("})();"));
 assert.match(HTML,/<div id="modelpanel" class="channels"><\/div><div id="agentpanel"/);
 assert.equal(MODELS_JS.trimStart(),readFileSync(new URL("./ui-models-source.js",import.meta.url),"utf8"));
});

test("both panels load independently and agent save calls the distinct write endpoint",async()=>{
 const requests=[];
 function element(tag,text,parent){
  const o={tag,textContent:text,children:[],value:"",events:{},setAttribute(){},
   appendChild(c){this.children.push(c);},replaceChildren(){this.children=[];},
   addEventListener(k,fn){this.events[k]=fn;}};
  if(parent)parent.appendChild(o);
  return o;
 }
 const roots={modelpanel:element("div"),agentpanel:element("div"),modelshint:element("p")};
 const model={id:"anthropic/claude-sonnet-5.5",label:"Sonnet",available:true,priceVerified:true,pricing:{prompt:{value:"0.002",unit:"token"}}};
 const policy={requested:{revision:0,models:{draft:model.id},reasoning:{draft:"low"}},effective:{revision:0},executorSync:"not_connected",pendingSync:false,roles:[{id:"draft",role:"Автор",capability:"text",candidates:[model.id]}],catalogue:[model],reasoningLevels:["low"],history:[]};
 const agent={settings:{version:0,model:model.id,reasoning:"low",perCallRub:2,dailyRub:10},models:[model],reasoningLevels:["low"],history:[]};
 const context={by:id=>roots[id],coreEl:element,window:{confirm:()=>true,alert:e=>{throw Error(e);}},loadAgent:async()=>{},coreRequest:async(path,body)=>{
  requests.push({path,body});
  if(path==="/api/core/models")return policy;
  if(path==="/api/core/agent/settings")return agent;
  if(path==="/api/core/agent/settings/write")return {settings:agent.settings};
  throw Error("unexpected route "+path);
 }};
 runInNewContext(MODELS_JS+"\nglobalThis.load=loadModels;",context);
 await context.load();
 assert.ok(roots.modelpanel.children.length>0);
 assert.ok(roots.agentpanel.children.length>0);
 const block=roots.agentpanel.children[0];
 for(const c of block.children){if(c.tag==="select")c.value=c.children[0].value;}
 const save=block.children.find(c=>c.tag==="button");
 await save.events.click();
 const sent=requests.find(r=>r.path==="/api/core/agent/settings/write");
 assert.equal(sent.body.model,model.id);
 assert.equal(sent.body.perCallRub,2);
 assert.equal(sent.body.dailyRub,10);
 assert.equal(sent.body.confirm,true);
});
