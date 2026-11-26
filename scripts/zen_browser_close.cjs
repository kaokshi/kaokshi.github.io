// No credentials, session identifiers or raw provider responses in logs.
async function confirmClose({sid,root,call,pause,now=Date.now}){
  const deadline=now()+25000;
  const detail=root.replace(/\/browser$/,'/session')+'/'+sid;
  let accepted=false;
  let status;
  for(let i=0;i<3;i++){
    try{
      const data=await call(root+'/'+sid,'DELETE',3000);
      status=(data.result||data).status;
      if(!['closing','closed'].includes(status))throw Error('UNKNOWN_CLOSE_STATE');
      accepted=true;break;
    }catch(e){if(i===2)throw e;await pause(250);}
  }
  if(!accepted)throw Error('CLOSE_NOT_REQUESTED');
  for(let i=0;i<12 && now()<deadline;i++){
    let row;
    try {
      const data=await call(detail,'GET',2000);
      row=Object.hasOwn(data,'result')?data.result:data;
    }catch(e){
      if(!['HTTP_404','HTTP_429','HTTP_502','HTTP_503','HTTP_504'].includes(e.message))throw e;
    }
    if(row&&row.sessionId===sid&&Number.isFinite(row.startTime)&&Number.isFinite(row.endTime)&&row.endTime>=row.startTime&&row.endTime>0){
      if(row.closeReason!==1&&row.closeReasonText!=='NormalClosure')throw Error('NOT_NORMAL_CLOSURE');
      return true;
    }
    // The explicit terminal response is useful, but is not a substitute for
    // provider end-time evidence in this acceptance test.
    await pause(1500);
  }
  throw Error('CLOSE_UNCONFIRMED');
}
module.exports={confirmClose};
