import {test} from "node:test";
import assert from "node:assert/strict";
import {admitClaudy,finishClaudy} from "./claudy-admission.mjs";
const empty=()=>({starts:[],active:[]});
test("three production slots preserve chat and developer slots",()=>{
 let s=empty();
 for(let i=0;i<3;i++)s=admitClaudy(s,"production","p"+i,100).state;
 assert.equal(admitClaudy(s,"production","p3",100).reason,"concurrency_limit");
 s=admitClaudy(s,"chat","c",100).state;
 s=admitClaudy(s,"development","d",100).state;
 assert.equal(s.active.length,5);
 assert.equal(admitClaudy(s,"chat","c2",100).allowed,false);
 assert.equal(admitClaudy(s,"chat","c",100).reason,"already_active");
});
test("rolling minute limit counts completed requests",()=>{
 let s=empty();
 for(let i=0;i<25;i++){s=admitClaudy(s,"chat","c"+i,100).state;s=finishClaudy(s,"c"+i,{terminal:true,now:100});}
 assert.equal(admitClaudy(s,"chat","next",60099).reason,"minute_limit");
 assert.equal(admitClaudy(s,"chat","next",60100).allowed,true);
});
test("ambiguous requests keep slot, terminal retry-after pauses all roles",()=>{
 let s=admitClaudy(empty(),"chat","c",100).state;
 assert.deepEqual(finishClaudy(s,"c",{terminal:false,now:100}),s);
 s=finishClaudy(s,"c",{terminal:true,retryAfterSeconds:5,now:100});
 assert.equal(admitClaudy(s,"development","d",5099).reason,"provider_backoff");
 assert.equal(admitClaudy(s,"development","d",5100).allowed,true);
});
