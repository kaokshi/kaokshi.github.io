import test from 'node:test';
import assert from 'node:assert/strict';
import worker,{authorized,command} from './worker.mjs';
const env={TELEGRAM_BOT_TOKEN:"test",TELEGRAM_OWNER_ID:"123",TELEGRAM_WEBHOOK_SECRET:"a".repeat(32)};
const message={chat:{type:"private",id:123},from:{id:123,is_bot:false},text:"/start"};
test("owner only, private chat",()=>{
 assert.equal(authorized(message,env),true);
 assert.equal(authorized({...message,from:{id:124,is_bot:false}},env),false);
 assert.equal(authorized({...message,chat:{type:"group",id:123}},env),false);
 assert.equal(authorized(message,{...env,TELEGRAM_OWNER_ID:""}),false);
});
test("webhook rejects wrong secret",async()=>{
 const r=await worker.fetch(new Request("https://example.test/telegram",{method:"POST",body:"{}"}),env);
 assert.equal(r.status,403);
});
test("unauthorized updates ignored without upstream calls",async()=>{
 const r=await worker.fetch(new Request("https://example.test/telegram",{method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":env.TELEGRAM_WEBHOOK_SECRET},body:JSON.stringify({message:{...message,from:{id:124,is_bot:false}}})}),env);
 assert.equal(r.status,200);
});
test("no paid or publishing command",async()=>{
 assert.match(await command("/publish",env),/не поддерживается/);
 assert.match(await command("/generate",env),/не поддерживается/);
 assert.match(await command("/balance",env),/не настроен/);
});
test("models are explicitly planned",async()=>assert.match(await command("/models",env),/не подтверждение/));
