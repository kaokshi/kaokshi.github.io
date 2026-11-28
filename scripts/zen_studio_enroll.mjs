// Run in a trusted Linux desktop terminal. Login happens in the visible browser.
import {mkdtemp, rm} from "node:fs/promises";
import {createRequire} from "node:module";
import {createInterface} from "node:readline/promises";
import {bridge, assertStudio, channelRoutes, workerOrigin} from "./zen_studio_sync.mjs";
import {PRIMARY_CHANNEL, channelTitle, validChannel} from "../telegram-control/studio-channels.mjs";
import {sealStudioState, studioRoute} from "../telegram-control/studio-auth.mjs";
const require = createRequire(import.meta.url);

async function main(env) {
 const origin = workerOrigin(env.ZEN_STUDIO_WORKER_URL);
 if (!/^[a-f0-9]{64}$/i.test(env.ZEN_STUDIO_ENROLL_SECRET || "")) throw Error();
 const action = process.argv[2];
 // Optional channel key from the registry; each channel can be on its own Yandex account.
 const channel = process.argv[3] || PRIMARY_CHANNEL;
 if (!validChannel(channel)) throw Error();
 if (action === "revoke") {
  await bridge(origin, env.ZEN_STUDIO_ENROLL_SECRET, "revoke", process.argv[3] ? {channel} : {});
  console.log("Авторизация удалена из хранилища.");
  return;
 }
 if (action !== "enroll" || process.platform !== "linux" || !process.stdin.isTTY ||
     !/^[a-f0-9]{64}$/i.test(env.ZEN_STUDIO_VAULT_KEY || "")) throw Error();
 const route = studioRoute(channelRoutes(env)[channel]);
 const profile = await mkdtemp("/dev/shm/zen-studio-");
 let context;
 const terminal = createInterface({input: process.stdin, output: process.stdout});
 const abort = new AbortController();
 const stop = () => abort.abort();
 const timer = setTimeout(stop, 12 * 60 * 1000);
 process.once("SIGTERM", stop);
 process.once("SIGINT", stop);
 terminal.on("SIGINT", stop);
 try {
  const {chromium} = require("playwright-core");
  const safeEnv = {...env};
  for (const name of Object.keys(safeEnv)) if (/(?:TOKEN|SECRET|VAULT_KEY)/i.test(name)) delete safeEnv[name];
  context = await chromium.launchPersistentContext(profile, {headless: false, executablePath: env.ZEN_STUDIO_CHROMIUM,
    acceptDownloads: false, chromiumSandbox: true, env: safeEnv, timeout: 15000});
  const page = context.pages()[0] || await context.newPage();
  await page.goto(route.url, {waitUntil: "domcontentloaded", timeout: 15000});
  await terminal.question("Войдите в Дзен аккаунтом владельца канала «" + channelTitle(channel) + "» и откройте его статистику. Затем нажмите Enter. ", {signal: abort.signal});
  // After login Dzen may show another page; open the channel's statistics in the same
  // signed-in window. The identity check below stays exact.
  const here = new URL(page.url());
  if (here.origin + here.pathname !== route.url)
   await page.goto(route.url + "?statType=publications", {waitUntil: "domcontentloaded", timeout: 15000});
  await assertStudio(page, route);
  const state = await context.storageState({indexedDB: true});
  const envelope = await sealStudioState(state, env.ZEN_STUDIO_VAULT_KEY, route.url);
  await bridge(origin, env.ZEN_STUDIO_ENROLL_SECRET, "enroll", {generation: crypto.randomUUID(), envelope,
   ...(channel === PRIMARY_CHANNEL ? {} : {channel})});
  console.log("Шифрованная сессия сохранена.");
 } finally {
  clearTimeout(timer);
  process.removeListener("SIGTERM", stop);
  process.removeListener("SIGINT", stop);
  terminal.close();
  try { await context?.close(); } finally { await rm(profile, {recursive: true, force: true}); }
 }
}
main(process.env).catch(() => {
 console.error("Не удалось завершить операцию с авторизацией. Проверьте защищённую конфигурацию и официальную страницу Дзена.");
 process.exitCode = 1;
});
