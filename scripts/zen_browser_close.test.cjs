const {test}=require('node:test');
const assert=require('node:assert/strict');
const {confirmClose}=require('./zen_browser_close.cjs');
const root='https://example.invalid/devtools/browser',sid='synthetic';
const ended={sessionId:sid,startTime:1000,endTime:2000,closeReason:1};
function fake(rows,status='closing'){
  let time=0,reads=0,deletes=0;
  return {opts:{sid,root,now:()=>time,pause:async ms=>{time+=ms;},call:async(url,method)=>{
    if(method==='DELETE'){deletes++;assert.equal(url,root+'/'+sid);return {result:{status}};}
    assert.equal(url,'https://example.invalid/devtools/session/'+sid);
    const item=rows[Math.min(reads++,rows.length-1)];
    if(item instanceof Error)throw item;
    return {result:item};
  }},counts:()=>({reads,deletes})};
}
test('delayed detail confirms explicit closure',async()=>{
  const f=fake([null,new Error('HTTP_404'),{sessionId:sid,startTime:1000},ended]);
  assert.equal(await confirmClose(f.opts),true);
  assert.deepEqual(f.counts(),{reads:4,deletes:1});
});
test('closed response still requires terminal evidence',async()=>{
  const f=fake([null],'closed');
  await assert.rejects(confirmClose(f.opts),/CLOSE_UNCONFIRMED/);
});
test('wrong session cannot confirm',async()=>{
  const f=fake([{...ended,sessionId:'other'}]);
  await assert.rejects(confirmClose(f.opts),/CLOSE_UNCONFIRMED/);
});
test('timeout close rejected',async()=>{
  const f=fake([{...ended,closeReason:2,closeReasonText:'Timeout'}]);
  await assert.rejects(confirmClose(f.opts),/NOT_NORMAL_CLOSURE/);
});
test('invalid delete response fails closed',async()=>{
  const f=fake([ended],'unknown');
  await assert.rejects(confirmClose(f.opts),/UNKNOWN_CLOSE_STATE/);
});

test('history can prove a closed browser when detail has no record', async () => {
  const f = fake([null]);
  const observed = [];
  assert.equal(await confirmClose({...f.opts, latest: async () => [ended],
                                  observe: value => observed.push(value)}), true);
  assert.deepEqual(observed, ['delete_acknowledged', 'detail_empty', 'history_terminal']);
});
