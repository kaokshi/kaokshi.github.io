import {test} from "node:test";
import assert from "node:assert/strict";
import {readModules, relevantSettings, studioUpload} from "./deploy-studio.mjs";

const shared = ["entry.mjs", "worker.mjs", "miniapp.mjs", "ui.mjs"];
const add = ["studio-analytics.mjs", "studio-auth.mjs"];

test("content upload includes only Studio changes and retains every other live module", async () => {
 const live = new Map(shared.map(name => [name, new Blob([name])]));
 live.set("production.mjs", new Blob(["live executor"]));
 live.set("unknown-live-extension.mjs", new Blob(["extra"]));
 const base = new Map(shared.map(name => [name, name]));
 const next = new Map([...shared, ...add].map(name => [name, "studio " + name]));
 const upload = await studioUpload(live, base, next);
 assert.deepEqual(JSON.parse(upload.get("metadata")), {main_module: "entry.mjs"});
 assert.equal(await upload.get("production.mjs").text(), "live executor");
 assert.equal(await upload.get("unknown-live-extension.mjs").text(), "extra");
 for (const name of [...shared, ...add]) assert.equal(await upload.get(name).text(), next.get(name));
 await assert.rejects(studioUpload(live, new Map(), next), /worker_base_mismatch_entry/);
});

test("content parser requires named modules with a valid entry point", async () => {
 const form = new FormData();
 form.set("entry.mjs", new Blob(["export default {};"], {type: "application/javascript+module"}), "entry.mjs");
 assert.ok((await readModules(new Response(form))).has("entry.mjs"));
 await assert.rejects(readModules(new Response("public script")), /worker_modules_not_multipart/);
});

test("settings verification tracks the executor and namespace including all bindings", () => {
 const bindings = [
  {name: "REVIEW_JOURNAL", type: "durable_object_namespace", class_name: "ReviewJournal"},
  {name: "ZEN_CORE_EXECUTION_ENABLED", type: "plain_text", text: "true"},
 ];
 assert.equal(relevantSettings({bindings}), relevantSettings({bindings: [...bindings].reverse()}));
 assert.notEqual(relevantSettings({bindings}), relevantSettings({bindings: [bindings[0], {...bindings[1], text: "false"}]}));
 assert.throws(() => relevantSettings({bindings: []}), /worker_namespace_unverified/);
});
