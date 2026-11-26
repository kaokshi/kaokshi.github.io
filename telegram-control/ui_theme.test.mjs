import test from "node:test";
import assert from "node:assert/strict";
import {runInNewContext} from "node:vm";
import {HTML,CSS,JS} from "./ui.mjs";

// Synthetic fixture only: no owner data, no Telegram session, no network.
const STORAGE_KEY="zencontrol.theme";
const MARKER="const tabs=document.querySelectorAll";
const CUT=JS.indexOf(MARKER);

function element(dataset={}){
 const attributes=new Map(),listeners=new Map();
 return {
  dataset:{...dataset},
  textContent:"",
  setAttribute(name,value){attributes.set(name,String(value));},
  getAttribute(name){return attributes.has(name)?attributes.get(name):null;},
  addEventListener(type,handler){const list=listeners.get(type)||[];list.push(handler);listeners.set(type,list);},
  fire(type="click"){for(const handler of listeners.get(type)||[])handler({type});}
 };
}

function environment({stored,colorScheme=null,systemDark=false,storage="ok",telegram=true,overrides=[]}={}){
 const data=new Map();
 if(typeof stored==="string")data.set(STORAGE_KEY,stored);
 const ok={getItem:key=>data.has(key)?data.get(key):null,setItem:(key,value)=>data.set(key,String(value)),removeItem:key=>data.delete(key)};
 const blocked={getItem(){throw Error("storage blocked");},setItem(){throw Error("storage blocked");},removeItem(){throw Error("storage blocked");}};
 const win={matchMedia:query=>({matches:systemDark&&String(query).includes("dark"),addEventListener(){}})};
 if(storage==="ok")win.localStorage=ok;
 if(storage==="blocked")win.localStorage=blocked;
 const calls={header:[],background:[],events:[]};
 if(telegram)win.Telegram={WebApp:{colorScheme,ready(){},expand(){},setHeaderColor:value=>calls.header.push(value),setBackgroundColor:value=>calls.background.push(value),onEvent:name=>calls.events.push(name)}};
 const root={dataset:{}};
 const toggle=element(),status=element();
 const choices=["auto","light","dark"].map(name=>element({themeChoice:name}));
 const extra=overrides.map(name=>element({themeChoice:name}));
 const document={
  documentElement:root,
  getElementById(id){if(id==="themetoggle")return toggle;if(id==="themestatus")return status;return element();},
  querySelectorAll(selector){return selector==="[data-theme-choice]"?[...choices,...extra]:[];}
 };
 return {win,document,root,toggle,status,choices,extra,data,calls};
}

function boot(env){
 assert.ok(CUT>0,"theme controller must be initialised before tab wiring");
 const context={window:env.win,document:env.document,location:{hash:"",search:""},URLSearchParams,Intl,Date,JSON,setTimeout,console};
 const expose=";globalThis.__theme={storedTheme,saveStoredTheme,resolveTheme,systemScheme,telegramScheme};";
 runInNewContext(JS.slice(0,CUT)+expose+"})();",context);
 return context.__theme;
}

test("CSS defines separate light and dark palettes with the shared accent",()=>{
 assert.match(CSS,/color-scheme:light/);
 assert.match(CSS,/:root\[data-theme=dark\]\{color-scheme:dark;--bg:#111218;--surface:#1d1f28/);
 assert.match(CSS,/;--txt:#f4f3ee;--muted:#b4b2a8;--accent:#f2cd53/);
 assert.match(CSS,/--bg:#f8f7f2/);
 assert.match(CSS,/--txt:#27251e/);
 assert.match(CSS,/:root\{--accent-fill:#f2cd53\}/);
 assert.match(CSS,/\[data-theme=dark\] article\{background:var\(--surface\)/);
 assert.match(CSS,/\[data-theme=dark\] \.theme-options button/);
 assert.match(CSS,/\[data-theme=dark\] nav button\.active/);
 // Mobile navigation bar keeps its own dark surface and safe-area padding.
 assert.match(CSS,/@media\(max-width:750px\)\{\[data-theme=dark\] aside\{background:#16171ff5/);
 assert.match(CSS,/env\(safe-area-inset-bottom\)/);
});

test("HTML exposes a theme toggle and an explicit three-way selector without external resources",()=>{
 assert.match(HTML,/<meta name="color-scheme" content="light dark">/);
 assert.match(HTML,/id="themetoggle" type="button" class="iconbtn" aria-label="Тёмная тема" aria-pressed="false"/);
 assert.match(HTML,/role="group" aria-label="Тема оформления"/);
 for(const name of ["auto","light","dark"])assert.match(HTML,new RegExp(`data-theme-choice="${name}"`));
 assert.match(HTML,/data-theme-choice="auto" aria-pressed="true"/);
 assert.match(HTML,/id="themestatus" role="status"/);
 assert.match(HTML,/script src="https:\/\/telegram\.org\/js\/telegram-web-app\.js"/);
 assert.doesNotMatch(HTML,/<link[^>]+href="https?:/);
 assert.doesNotMatch(HTML,/<img[^>]+src="https?:/);
 assert.equal((HTML.match(/<script/g)||[]).length,2);
});

test("theme controller is local-only and does not touch the editorial API",()=>{
 assert.doesNotThrow(()=>new Function(JS));
 assert.ok(CUT>0);
 const themeCode=JS.slice(0,CUT);
 assert.match(themeCode,/zencontrol\.theme/);
 assert.doesNotMatch(themeCode,/fetch\(/);
 assert.doesNotMatch(themeCode,/\/api\//);
 assert.match(JS,/app\?\.onEvent\?\.\("themeChanged"/);
 assert.match(JS,/app\?\.setHeaderColor\(dark\?"#1d1f28":"#ffffff"\)/);
});

test("light is the safe default when Telegram, storage and media queries are absent",()=>{
 const env=environment({telegram:false,storage:"none"});
 boot(env);
 assert.equal(env.root.dataset.theme,"light");
 assert.equal(env.toggle.getAttribute("aria-pressed"),"false");
 assert.equal(env.toggle.getAttribute("aria-label"),"Тёмная тема");
 assert.equal(env.toggle.textContent,"☾");
 assert.match(env.status.textContent,/Тема как в Telegram: светлая/);
});

test("automatic theme follows Telegram and reports the resolved palette",()=>{
 const env=environment({colorScheme:"dark"});
 boot(env);
 assert.equal(env.root.dataset.theme,"dark");
 assert.equal(env.toggle.getAttribute("aria-pressed"),"true");
 assert.equal(env.toggle.getAttribute("aria-label"),"Тёмная тема");
 assert.equal(env.toggle.getAttribute("title"),"Включить светлую тему");
 assert.equal(env.toggle.textContent,"☀");
 assert.match(env.status.textContent,/тёмная/);
 assert.equal(env.calls.header.at(-1),"#1d1f28");
 assert.equal(env.calls.background.at(-1),"#111218");
 assert.ok(env.calls.events.includes("themeChanged"));
 for(const value of [...env.calls.header,...env.calls.background])assert.match(value,/^#[0-9a-f]{6}$/);
});

test("stored preference wins over the Telegram scheme and survives reboots",()=>{
 const saved=environment({stored:"light",colorScheme:"dark"});
 boot(saved);
 assert.equal(saved.root.dataset.theme,"light");
 assert.equal(saved.toggle.getAttribute("aria-pressed"),"false");
 assert.equal(saved.data.get(STORAGE_KEY),"light");
 const dark=environment({stored:"dark",colorScheme:"light"});
 boot(dark);
 assert.equal(dark.root.dataset.theme,"dark");
 assert.equal(dark.choices.find(choice=>choice.dataset.themeChoice==="dark").getAttribute("aria-pressed"),"true");
 assert.equal(dark.choices.find(choice=>choice.dataset.themeChoice==="light").getAttribute("aria-pressed"),"false");
});

test("unknown or malformed stored values fall back to automatic without crashing",()=>{
 for(const stored of ["neon","DARK","", "x".repeat(4000),"__proto__"]){
  const env=environment({stored,colorScheme:"dark"});
  assert.doesNotThrow(()=>boot(env));
  assert.equal(env.root.dataset.theme,"dark");
  assert.equal(env.data.get(STORAGE_KEY),stored,"storage must not be rewritten on load");
  assert.equal(env.choices.find(choice=>choice.dataset.themeChoice==="auto").getAttribute("aria-pressed"),"true");
 }
 const corrupt=environment({stored:"neon"});
 const theme=boot(corrupt);
 assert.equal(theme.storedTheme(),"auto");
});

test("blocked local storage degrades to a session-only theme",()=>{
 const env=environment({storage:"blocked",colorScheme:"dark"});
 const theme=boot(env);
 assert.equal(env.root.dataset.theme,"dark");
 assert.equal(theme.storedTheme(),"auto");
 assert.equal(theme.saveStoredTheme("dark"),false);
 const light=env.choices.find(choice=>choice.dataset.themeChoice==="light");
 assert.doesNotThrow(()=>light.fire());
 assert.equal(env.root.dataset.theme,"light");
 assert.equal(light.getAttribute("aria-pressed"),"true");
 assert.match(env.status.textContent,/Браузер запретил сохранение/);
 assert.equal(env.data.has(STORAGE_KEY),false);
});

test("selector and header toggle persist exactly one documented key",()=>{
 const env=environment({colorScheme:"light"});
 boot(env);
 const dark=env.choices.find(choice=>choice.dataset.themeChoice==="dark");
 dark.fire();
 assert.equal(env.data.get(STORAGE_KEY),"dark");
 assert.equal(env.root.dataset.theme,"dark");
 assert.equal(dark.getAttribute("aria-pressed"),"true");
 assert.equal(env.calls.header.at(-1),"#1d1f28");
 env.toggle.fire();
 assert.equal(env.data.get(STORAGE_KEY),"light");
 assert.equal(env.root.dataset.theme,"light");
 assert.equal(env.toggle.getAttribute("aria-pressed"),"false");
 env.toggle.fire();
 assert.equal(env.data.get(STORAGE_KEY),"dark");
 assert.deepEqual([...env.data.keys()],[STORAGE_KEY]);
});

test("toggle switches away from the resolved automatic theme",()=>{
 const env=environment({colorScheme:"dark"});
 boot(env);
 env.toggle.fire();
 assert.equal(env.data.get(STORAGE_KEY),"light");
 assert.equal(env.root.dataset.theme,"light");
 env.toggle.fire();
 assert.equal(env.data.get(STORAGE_KEY),"dark");
});

test("unexpected selector values fall back to automatic",()=>{
 const env=environment({overrides:["neon",""]});
 boot(env);
 for(const button of env.extra){
  assert.doesNotThrow(()=>button.fire());
  assert.equal(env.data.get(STORAGE_KEY),"auto");
  assert.equal(env.root.dataset.theme,"light");
 }
});

test("resolveTheme prefers explicit preference, then Telegram, then the media query",()=>{
 const env=environment({colorScheme:"light",systemDark:true});
 const theme=boot(env);
 assert.equal(theme.resolveTheme("dark"),"dark");
 assert.equal(theme.resolveTheme("light"),"light");
 assert.equal(theme.resolveTheme("auto"),"light","Telegram scheme outranks the CSS media query");
 assert.equal(theme.resolveTheme("neon"),"light");
 const withoutTelegram=environment({telegram:false,systemDark:true});
 const fallback=boot(withoutTelegram);
 assert.equal(fallback.telegramScheme(),null);
 assert.equal(fallback.resolveTheme("auto"),"dark");
 const bare=environment({telegram:false,storage:"none"});
 assert.equal(boot(bare).resolveTheme("auto"),"light");
});

test("missing document root leaves the app running without touching the DOM",()=>{
 const env=environment({colorScheme:"dark"});
 env.document.documentElement=undefined;
 assert.doesNotThrow(()=>boot(env));
 assert.equal(env.toggle.getAttribute("aria-pressed"),null);
 assert.equal(env.calls.header.length,0);
});
