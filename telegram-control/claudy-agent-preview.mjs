// Native response compatibility with the existing managing-agent proposal validator.
// Pure helper: it does not submit jobs, change settings, authorize spending or dispatch.
import {claudyResponse} from "./claudy-adapter.mjs";
import {parseReply} from "./agent.mjs";
export function validateClaudyAgentReply(model,payload,response){
 const observed=claudyResponse(model,payload,response);
 if(!observed.complete)return {accepted:false,reason:"incomplete_reply",costVerified:false};
 try{
  const parsed=parseReply({choices:[{finish_reason:"stop",message:{content:observed.reply}}]});
  return {accepted:true,parsed,usage:observed.usage,estimatedRub:observed.estimatedRub,costVerified:false,executionAuthorized:false};
 }catch{return {accepted:false,reason:"invalid_agent_reply",costVerified:false};}
}
