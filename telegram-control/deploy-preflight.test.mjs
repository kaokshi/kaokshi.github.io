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
test("upgrade refuses to rotate a missing existing webhook secret",()=>{
 const run=spawnSync(process.execPath,[script.pathname],{env:{PATH:process.env.PATH,ZEN_CONTROL_CONFIRM_UPDATE:"true",ZEN_CORE_INTAKE_SECRET:"a".repeat(64)},encoding:"utf8",timeout:5000});
 assert.equal(run.status,1);assert.match(run.stdout+run.stderr,/existing_webhook_secret_required/);
});
test("deployment is manual and carries all imported modules",()=>{
 const workflow=readFileSync(new URL("../.github/workflows/telegram-control-deploy.yml",import.meta.url),"utf8");
 assert.match(workflow,/workflow_dispatch:/);assert.doesNotMatch(workflow,/\n  push:/);
 const deploy=readFileSync(script,"utf8");for(const module of ["account.mjs","intake.mjs","receipt.mjs","recovery.mjs"])assert.ok(deploy.includes('"'+module+'"'));
});
