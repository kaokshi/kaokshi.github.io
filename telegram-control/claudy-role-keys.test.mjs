import {test} from "node:test";
import assert from "node:assert/strict";
import {claudyRoleKey,claudyRoleStatus} from "./claudy-role-keys.mjs";
test("each role uses only its designated credential",()=>{
 const env={CLAUDY_API_KEY:"legacy",CLAUDY_SONNET_API_KEY:"chat",CLAUDY_OPUS_API_KEY:"dev",CLAUDY_EDITORIAL_API_KEY:"editorial"};
 assert.equal(claudyRoleKey(env,"chat"),"chat");
 assert.equal(claudyRoleKey(env,"development"),"dev");
 assert.equal(claudyRoleKey(env,"production"),"editorial");
 assert.equal(claudyRoleKey(env,"unknown"),null);
 assert.equal(claudyRoleKey({CLAUDY_API_KEY:"legacy"},"chat"),null);
 assert.deepEqual(claudyRoleStatus(env),{chat:true,development:true,production:true,distinct:true,liveEnabled:false});
 assert.equal(claudyRoleStatus({...env,CLAUDY_OPUS_API_KEY:"chat"}).distinct,false);
});
