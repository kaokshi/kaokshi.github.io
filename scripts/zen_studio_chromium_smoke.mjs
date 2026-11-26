// Exercise the real Chromium CDP response stream and responsive ZEN CONTROL UI locally.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {once} from "node:events";
import {createRequire} from "node:module";
import {HTML, CSS, JS} from "../telegram-control/ui.mjs";
import {workbook} from "./zen_studio_sync.mjs";
const require = createRequire(import.meta.url);
const {chromium} = require("playwright-core");
const binary = Buffer.from([80, 75, 3, 4, 0, 250, 15, 50, 141, 23]);
const fixture = {authorization: "ready", acceptedChannels: 3, busy: false, manualAvailable: true,
 lastSuccess: "2026-11-26T12:00:00Z", quota: {remainingMs: 302000, limitMs: 480000, checkedAt: null},
 channels: ["science", "legends", "cinema"].map(channel => ({channel, history: [], latest: {
  start: "2026-11-20", end: "2026-11-26",
  dzen: {impressions: 12, opens: 8, reads_and_views: 4, watch_minutes: 2}}}))};
const server = createServer((req, res) => {
 const path = new URL(req.url, "http://fixture.invalid").pathname;
 if (path === "/fixture-export") {
  res.writeHead(200, {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
   "Content-Disposition": "attachment; filename=fixture.xlsx"});
  res.end(binary);
 } else if (path === "/fixture-workbook") {
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end('<a href="/fixture-export">Скачать XLSX</a>');
 } else if (path === "/") {
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.end(HTML);
 } else if (path === "/app.css") {
  res.setHeader("Content-Type", "text/css");
  res.end(CSS);
 } else if (path === "/app.js") {
  res.setHeader("Content-Type", "text/javascript");
  res.end(JS);
 } else {
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(path === "/api/analytics" ? fixture : {balance: null}));
 }
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const origin = "http://127.0.0.1:" + server.address().port;
let browser;
try {
 browser = await chromium.launch({executablePath: process.env.ZEN_STUDIO_CHROMIUM || "/usr/bin/chromium"});
 const context = await browser.newContext({acceptDownloads: false, viewport: {width: 360, height: 800}});
 context.setDefaultTimeout(10000);
 const page = await context.newPage();
 await page.goto(origin + "/fixture-workbook");
 assert.deepEqual(await workbook(context, page, "/fixture-export", "Скачать XLSX", origin), binary);
 await context.route(url => url.origin !== origin, route => route.abort());
 await page.goto(origin + "/?tgWebAppData=synthetic");
 await page.getByRole("button", {name: "Статистика", exact: true}).click();
 await page.getByRole("heading", {name: "Кадр за Кадром"}).waitFor();
 assert.equal(await page.getByRole("button", {name: "Обновить статистику"}).isEnabled(), true);
 assert.match(await page.locator("#dzen-sync-status").textContent(), /302 \/ 480 с/);
 const background = await page.locator("body").evaluate(el => getComputedStyle(el).backgroundColor);
 await page.locator("#themetoggle").click();
 assert.notEqual(await page.locator("body").evaluate(el => getComputedStyle(el).backgroundColor), background);
 assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
 console.log("Chromium verified binary CDP export, three-channel UI, light/dark themes and mobile layout.");
} finally {
 await browser?.close();
 server.close();
}
