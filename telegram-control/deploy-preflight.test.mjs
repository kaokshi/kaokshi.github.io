import {test} from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {readFileSync} from "node:fs";
const script=new URL("./deploy.mjs",import.meta.url);
test("deployment requires explicit confirmation before contacting any service",()=>{
 const run=spawnSync(process.execPath,[script.pathname],{env:{PATH:process.env.PATH},encoding:"utf8",timeout:5000});
 assert.equal(run.status,1);assert.match(run.stdout+run.stderr,/explicit_update_confirmation_required/);
});
test("confirmed upgrade without dedicated bridge secret fails before network",()=>{
 const run=spawnSync(process.execPath,[script.pathname],{env:{PATH:process.env.PATH,ZEN_CONTROL_CONFIRM_UPDATE:"true"},encoding:"utf8",timeout:5000});
 assert.equal(run.status,1);assert.match(run.stdout+run.stderr,/intake_secret_required/);
});
test("upgrade rejects malformed supplied webhook secret before network",()=>{
 const run=spawnSync(process.execPath,[script.pathname],{env:{PATH:process.env.PATH,ZEN_CONTROL_CONFIRM_UPDATE:"true",ZEN_CORE_INTAKE_SECRET:"a".repeat(64),TELEGRAM_WEBHOOK_SECRET:"bad"},encoding:"utf8",timeout:5000});
 assert.equal(run.status,1);assert.match(run.stdout+run.stderr,/existing_webhook_secret_invalid/);
});
test("deployment is manual and carries all imported modules",()=>{
 const workflow=readFileSync(new URL("../.github/workflows/telegram-control-deploy.yml",import.meta.url),"utf8");
 assert.match(workflow,/workflow_dispatch:/);assert.doesNotMatch(workflow,/\n  push:/);
 const deploy=readFileSync(script,"utf8");for(const module of ["account.mjs","intake.mjs","receipt.mjs","recovery.mjs"])assert.ok(deploy.includes('"'+module+'"'));
});

import {activeDeployment,rollbackBody,latestVersion,readBridgeProbe} from "./deploy-guard.mjs";
const prior={id:"10000000-0000-4000-8000-000000000001",version:"20000000-0000-4000-8000-000000000001"};
const next={id:"10000000-0000-4000-8000-000000000002",version:"20000000-0000-4000-8000-000000000002"};
const deployment=(d,created_on)=>({id:d.id,created_on,versions:[{version_id:d.version,percentage:100}]});
test("rollback refuses unknown history, split traffic or an intervening deployment",()=>{
 const old=deployment(prior,"2026-11-24T10:00:00Z"),fresh=deployment(next,"2026-11-24T11:00:00Z");
 assert.deepEqual(activeDeployment({deployments:[old,fresh]}),next);
 for(const data of [{},{deployments:[]},{deployments:[{...fresh,versions:[{version_id:next.version,percentage:50}]}]}])assert.throws(()=>activeDeployment(data));
 assert.throws(()=>rollbackBody(prior,prior,next));
 assert.deepEqual(rollbackBody(prior,next,next),{strategy:"percentage",versions:[{version_id:prior.version,percentage:100}]});
});
test("rollback requires explicit confirmation before network",()=>{
 const run=spawnSync(process.execPath,[new URL("./rollback.mjs",import.meta.url).pathname],{env:{PATH:process.env.PATH},encoding:"utf8",timeout:5000});
 assert.equal(run.status,1);assert.match(run.stdout,/explicit_rollback_confirmation_required/);
});

test("existing webhook secret is inherited strictly, never replaced or registered again",()=>{
 const deploy=readFileSync(script,"utf8");
 assert.match(deploy,/type:"inherit",name:"TELEGRAM_WEBHOOK_SECRET"/);
 assert.match(deploy,/bindings_inherit=strict/);
 assert.doesNotMatch(deploy,/telegram\("setWebhook"/);
 assert.match(deploy,/existing_webhook_binding_required/);
});

test("latest uploaded version is identified independently of deployment",()=>{
 assert.equal(latestVersion({items:[{id:prior.version,number:1},{id:next.version,number:2}]}),next.version);
 assert.throws(()=>latestVersion({items:[{id:prior.version,number:1},{id:next.version,number:1}]}));
 assert.throws(()=>latestVersion({items:[]}));
});

test("read-only bridge probes retry transient failures, but never authorization or malformed replies",async()=>{
 let calls=0;const pause=async()=>{};
 const valid=v=>v.executionEnabled===false;
 const result=await readBridgeProbe(async()=>++calls===1?new Response("",{status:503}):Response.json({executionEnabled:false}),valid,{pause});
 assert.equal(result.executionEnabled,false);assert.equal(calls,2);
 for(const status of [401,403,400,404,409]){
  calls=0;await assert.rejects(()=>readBridgeProbe(async()=>{calls++;return new Response("",{status});},valid,{pause}),new RegExp("bridge_http_"+status));assert.equal(calls,1);
 }
 calls=0;await assert.rejects(()=>readBridgeProbe(async()=>{calls++;return Response.json({executionEnabled:true});},valid,{pause}),/bridge_invalid_response/);assert.equal(calls,1);
 calls=0;await assert.rejects(()=>readBridgeProbe(async()=>{calls++;return new Response("",{status:503});},valid,{pause}),/bridge_http_503/);assert.equal(calls,4);
});

test("deployment verification waits for exact HTML, JS and CSS, never auth failures",async()=>{
 const {verifyDeployedAssets}=await import("./deploy-guard.mjs");
 let calls=0,waits=0;
 const expected={"/app":"new html","/app.js":"new js","/app.css":"new css"};
 await verifyDeployedAssets(async path=>new Response(++calls===1?"old html":expected[path]),expected,{pause:async()=>waits++});
 assert.equal(calls,4);assert.equal(waits,1);
 await assert.rejects(verifyDeployedAssets(async()=>new Response("",{status:401}),expected,{pause:async()=>{throw Error("retried auth");}}),/ui_asset_http_401/);
 await assert.rejects(verifyDeployedAssets(async()=>new Response("always old"),expected,{attempts:2,pause:async()=>{}}),/ui_assets_not_converged/);
});
