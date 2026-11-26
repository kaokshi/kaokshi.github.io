// Deploy the Studio modules without replacing Worker settings or runtime bindings.
import {execFileSync} from "node:child_process";
import {readFile} from "node:fs/promises";
import {createHmac} from "node:crypto";
import {pathToFileURL} from "node:url";
import {activeDeployment, latestVersion, readBridgeProbe, rollbackBody, verifyDeployedAssets} from "./deploy-guard.mjs";
import {HTML, CSS, JS} from "./ui.mjs";

const SCRIPT = "zen-editorial-control";
const BASE = "c6ec6392aa617f52e6115a1dd36875ba3c6145f6";
const MAIN = "entry.mjs";
const CHANGED = ["entry.mjs", "worker.mjs", "miniapp.mjs", "ui.mjs"];
const ADDED = ["studio-analytics.mjs", "studio-auth.mjs"];
const uuid = value => typeof value === "string" && /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(value);
const verify = (value, error) => { if (!value) throw Error(error); };

export async function readModules(response) {
 verify(/^multipart\/form-data\b/i.test(response.headers.get("content-type") || ""), "worker_modules_not_multipart");
 const data = await response.formData();
 const modules = new Map();
 for (const [name, body] of data) {
  if (name === "metadata") continue;
  verify(/^[a-zA-Z0-9_.\/-]{1,180}$/.test(name) && !name.includes("..") && !name.startsWith("/") &&
   typeof body !== "string" && typeof body.arrayBuffer === "function" && !modules.has(name),
  "worker_module_shape_invalid");
  modules.set(name, body);
 }
 verify(modules.has(MAIN) && modules.size < 200, "worker_module_entry_missing");
 return modules;
}

export async function studioUpload(live, base, candidate) {
 const upload = new FormData();
 upload.set("metadata", JSON.stringify({main_module: MAIN}));
 for (const [name, body] of live) {
  verify(!ADDED.includes(name), "studio_modules_already_present");
  upload.set(name, body, name);
 }
 for (const name of CHANGED) {
  const deployed = live.get(name);
  verify(deployed && (await deployed.text()) === base.get(name), "worker_base_mismatch_" + name.replace(".mjs", ""));
 }
 for (const name of [...CHANGED, ...ADDED]) {
  const source = candidate.get(name);
  verify(typeof source === "string" && source.length > 0, "candidate_source_missing");
  upload.set(name, new Blob([source], {type: "application/javascript+module"}), name);
 }
 return upload;
}

export function relevantSettings(settings) {
 verify(Array.isArray(settings?.bindings), "worker_bindings_unavailable");
 verify(settings.bindings.some(b => b.name === "REVIEW_JOURNAL" &&
  b.type === "durable_object_namespace" && b.class_name === "ReviewJournal"), "worker_namespace_unverified");
 verify(settings.bindings.some(b => b.name === "ZEN_CORE_EXECUTION_ENABLED" &&
  b.type === "plain_text" && ["true", "false"].includes(b.text)), "worker_executor_setting_unverified");
 return JSON.stringify({
  bindings: [...settings.bindings].sort((a, b) => a.name.localeCompare(b.name)).map(row =>
   Object.fromEntries(Object.entries(row).sort(([a], [b]) => a.localeCompare(b)))),
  compatibility_date: settings.compatibility_date,
  compatibility_flags: settings.compatibility_flags,
  limits: settings.limits,
  placement: settings.placement,
  observability: settings.observability,
  usage_model: settings.usage_model,
 });
}

function telegramInit(owner, token) {
 verify(/^[1-9]\d{0,15}$/.test(owner || "") && Number.isSafeInteger(Number(owner)) &&
  /^\d+:[A-Za-z0-9_-]+$/.test(token || ""), "owner_probe_config_missing");
 const params = new URLSearchParams({auth_date: String(Math.floor(Date.now() / 1000)),
  query_id: "studio-deploy-readonly", user: JSON.stringify({id: Number(owner), is_bot: false})});
 const source = [...params].sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => key + "=" + value).join("\n");
 const key = createHmac("sha256", "WebAppData").update(token).digest();
 params.set("hash", createHmac("sha256", key).update(source).digest("hex"));
 return params.toString();
}

export async function main(env, fetcher = fetch) {
 let stage = "configuration";
 let rollback;
 const root = "https://api.cloudflare.com/client/v4/accounts/" + env.CLOUDFLARE_ACCOUNT_ID + "/workers";
 const script = "/scripts/" + SCRIPT;
 const auth = {Authorization: "Bearer " + env.CLOUDFLARE_API_TOKEN};
 const request = (url, init = {}) => fetcher(url, {...init, redirect: "error", signal: AbortSignal.timeout(18000)});
 const cloud = async (path, init = {}) => {
  const response = await request(root + path, {...init, headers: {...auth, ...init.headers}});
  if (!response.ok) throw Error("cloudflare_http_" + response.status);
  return response;
 };
 const cf = async (path, init) => {
  const body = await (await cloud(path, init)).json();
  verify(body.success === true, "cloudflare_rejected");
  return body.result;
 };
 const deploymentPath = script + "/deployments";
 const versionsPath = script + "/versions?per_page=10&page=1";
 try {
  verify(/^[a-f0-9]{32}$/i.test(env.CLOUDFLARE_ACCOUNT_ID || "") && env.CLOUDFLARE_API_TOKEN, "cloudflare_config_missing");
  const deploy = env.ZEN_STUDIO_DEPLOY_MODE === "deploy";
  verify(deploy || env.ZEN_STUDIO_DEPLOY_MODE === "preflight", "unknown_deploy_mode");
  verify(!deploy || uuid(env.ZEN_STUDIO_EXPECTED_VERSION), "expected_version_required");
  stage = "read_current_deployment";
  const current = activeDeployment(await cf(deploymentPath));
  verify(latestVersion(await cf(versionsPath)) === current.version, "active_version_is_not_latest");
  verify(!deploy || current.version === env.ZEN_STUDIO_EXPECTED_VERSION, "active_version_changed");
  const configs = relevantSettings(await cf(script + "/settings"));
  const sub = await cf("/subdomain");
  verify(/^[a-z0-9][a-z0-9-]{0,62}$/.test(sub?.subdomain || ""), "worker_subdomain_unverified");
  const endpoint = "https://" + SCRIPT + "." + sub.subdomain + ".workers.dev";
  const readApp = (path, initData) => request(endpoint + path, {method: "POST",
   headers: {"Content-Type": "application/json"}, body: JSON.stringify({initData})});
  const owner = telegramInit(env.TELEGRAM_OWNER_ID, env.TELEGRAM_BOT_TOKEN);
  const release = body => body?.product === "ZEN CONTROL" && body.publicationAuthorized === false && body.rssEnabled === false;
  stage = "verify_existing_release";
  const beforeRelease = await readBridgeProbe(() => readApp("/api/release", owner), release);
  stage = "compare_live_modules";
  const live = await readModules(await cloud(script + "/content/v2"));
  const base = new Map(CHANGED.map(name => [name, execFileSync("git", ["show", BASE + ":telegram-control/" + name], {encoding: "utf8"})]));
  const candidate = new Map(await Promise.all([...CHANGED, ...ADDED].map(async name =>
   [name, await readFile(new URL("./" + name, import.meta.url), "utf8")])));
  const upload = await studioUpload(live, base, candidate);
  console.log("STUDIO_BASE_VERIFIED=" + current.version);
  if (!deploy) return;
  stage = "upload_studio_modules";
  const before = activeDeployment(await cf(deploymentPath));
  verify(before.version === current.version && before.id === current.id &&
   latestVersion(await cf(versionsPath)) === current.version &&
   configs === relevantSettings(await cf(script + "/settings")), "deployment_changed_after_preflight");
  await cf(script + "/content", {method: "PUT", body: upload,
   headers: {"CF-WORKER-MAIN-MODULE-PART": MAIN}});
  const updated = activeDeployment(await cf(deploymentPath));
  verify(updated.id !== current.id && updated.version !== current.version, "studio_deployment_unverified");
  rollback = {current, updated};
  stage = "verify_studio_deployment";
  await verifyDeployedAssets(path => request(endpoint + path + "?deploy=" + updated.version, {cache: "no-store"}),
   {"/app": HTML, "/app.css": CSS, "/app.js": JS});
  verify(configs === relevantSettings(await cf(script + "/settings")), "worker_settings_changed");
  const afterRelease = await readBridgeProbe(() => readApp("/api/release", owner), release);
  verify(JSON.stringify(beforeRelease) === JSON.stringify(afterRelease), "existing_release_changed");
  for (const path of ["/api/analytics", "/api/analytics/refresh"])
   verify((await readApp(path, "invalid")).status === 401, "studio_owner_gate_failed");
  const studio = await readBridgeProbe(() => readApp("/api/analytics", owner), value =>
   ["missing", "ready", "expired", "challenge", "invalid"].includes(value?.authorization) &&
   value.manualAvailable === false && value.quota?.limitMs === 480000 &&
   value.channels?.map(row => row.channel).join(",") === "science,legends,cinema");
  const privateRoute = await request(endpoint + "/internal/zen-studio/claim", {method: "POST", body: "{}"});
  verify(privateRoute.status === 401, "studio_bridge_gate_failed");
  rollback = undefined;
  console.log("STUDIO_DEPLOYED=" + updated.version);
  console.log("STUDIO_EXISTING_SETTINGS_VERIFIED");
  console.log("STUDIO_AUTH_STATUS=" + studio.authorization);
  console.log("STUDIO_APP_URL=" + endpoint + "/app");
 } catch (error) {
  const code = /^[a-z0-9_]{3,100}$/.test(error?.message || "") ? error.message : "operation_failed";
  console.log("STUDIO_DEPLOY_BLOCKED stage=" + stage + " reason=" + code);
  if (rollback) {
   try {
    const active = activeDeployment(await cf(deploymentPath));
    await cf(deploymentPath, {method: "POST", headers: {"Content-Type": "application/json"},
     body: JSON.stringify(rollbackBody(rollback.current, rollback.updated, active))});
    verify(activeDeployment(await cf(deploymentPath)).version === rollback.current.version, "rollback_unconfirmed");
    console.log("STUDIO_ROLLBACK_CONFIRMED");
   } catch { console.log("STUDIO_ROLLBACK_UNCONFIRMED"); }
  }
  process.exitCode = 1;
 }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main(process.env);
