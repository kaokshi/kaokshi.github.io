// Staged Claudy adapter. Pure request/response helpers; not wired to live chat.
// A usage-derived price is an estimate, not a settlement receipt.
const MODELS=Object.freeze({
 "anthropic/claude-sonnet-5.5":{id:"claude-sonnet-5-5",input:30,output:150},
 "anthropic/claude-opus-5.5":{id:"claude-opus-5-5",input:45,output:225},
});
export const CLAUDY_BASE="https://api.claudy.shop";
export const CLAUDY_MAX_OUTPUT=8000;
const count=x=>Number.isSafeInteger(x)&&x>=0;
export function claudyModel(model){return Object.hasOwn(MODELS,model)?MODELS[model]:null;}
export function claudyPayload(model,messages,reasoning="medium",maxTokens=CLAUDY_MAX_OUTPUT){
 const selected=claudyModel(model);
 if(!selected||!["off","low","medium","high"].includes(reasoning)||!Number.isSafeInteger(maxTokens)||maxTokens<1||maxTokens>32000)throw Error("claudy_configuration_invalid");
 if(!Array.isArray(messages)||messages.length!==2||messages[0]?.role!=="system"||messages[1]?.role!=="user"||messages.some(x=>typeof x.content!=="string"||!x.content.trim()))throw Error("claudy_messages_invalid");
 return {model:selected.id,system:messages[0].content,messages:[{role:"user",content:messages[1].content}],max_tokens:maxTokens,...(reasoning==="off"?{thinking:{type:"disabled"}}:{thinking:{type:"adaptive"},output_config:{effort:reasoning}})};
}
export function claudyBound(model,payload,perCallRub=5){
 const selected=claudyModel(model);
 if(!selected||payload?.model!==selected.id||!Number.isSafeInteger(payload.max_tokens)||payload.max_tokens<1||payload.max_tokens>32000||typeof perCallRub!=="number"||!Number.isFinite(perCallRub)||perCallRub<=0)return null;
 // Conservative byte estimate, 2x input for unexpected cache creation.
 const input=new TextEncoder().encode(JSON.stringify(payload)).length+1024;
 const rub=Math.ceil((input*selected.input*2+payload.max_tokens*selected.output)/1e6*100)/100;
 return {rub,withinLimit:rub<=perCallRub,inputBound:input,outputBound:payload.max_tokens,estimateOnly:true};
}
export function claudyHeaders(key){
 if(typeof key!=="string"||!key.trim())throw Error("claudy_key_missing");
 return {"x-api-key":key,"anthropic-version":"2023-06-01","Content-Type":"application/json","Accept":"application/json","X-Claudy-Cache":"off"};
}
export function claudyResponse(model,payload,response){
 const selected=claudyModel(model),bound=claudyBound(model,payload);
 if(!selected||!bound||!response||typeof response!=="object"||Array.isArray(response))throw Error("claudy_response_invalid");
 if(response.model!==selected.id)throw Error("claudy_model_mismatch");
 const u=response.usage;
 if(!u||!count(u.input_tokens)||!count(u.output_tokens)||!count(u.cache_read_input_tokens??0)||!count(u.cache_creation_input_tokens??0))throw Error("claudy_usage_invalid");
 const read=u.cache_read_input_tokens??0,write=u.cache_creation_input_tokens??0;
 if(u.input_tokens+read+write>bound.inputBound||u.output_tokens>bound.outputBound)throw Error("claudy_usage_over_bound");
 if(!Array.isArray(response.content))throw Error("claudy_content_invalid");
 const parts=response.content.filter(x=>x?.type==="text");
 if(parts.some(x=>typeof x.text!=="string"))throw Error("claudy_content_invalid");
 const reply=parts.map(x=>x.text).join("");
 const estimatedRub=(selected.input*(u.input_tokens+read*0.1+write*2)+selected.output*u.output_tokens)/1e6;
 return {reply,complete:response.stop_reason==="end_turn"&&Boolean(reply.trim()),
  stopReason:["end_turn","max_tokens","stop_sequence","tool_use","refusal"].includes(response.stop_reason)?response.stop_reason:"other",
  usage:{input:u.input_tokens,output:u.output_tokens,cacheRead:read,cacheWrite:write},
  estimatedRub,costVerified:false,provider:"claudy",model:response.model};
}
