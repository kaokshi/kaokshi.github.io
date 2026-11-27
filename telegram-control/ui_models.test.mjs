import test from "node:test";
import assert from "node:assert/strict";
import {HTML,JS} from "./ui.mjs";

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
 const body=JS.slice(start,JS.indexOf("async function loadBudget()",start));
 assert.match(body,/coreRequest\("\/api\/core\/models"\)/);
 assert.match(body,/d\.requested\.revision/);
 assert.match(body,/d\.pendingSync/);
 assert.match(body,/d\.effective\.revision/);
 assert.match(body,/d\.roles/);
 // A failure must not leave a stale or misleading claim on screen.
 assert.match(body,/Политика моделей недоступна/);
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
