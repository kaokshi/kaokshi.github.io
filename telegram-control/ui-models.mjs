// The model policy and agent settings UI source. It is embedded into the page
// bundle by ui.mjs. The source holds no backtick and no template interpolation,
// so it is carried as a raw template literal without hand-escaping.
export const MODELS_JS = String.raw`
async function loadModels(){
 const policyRoot=by("modelpanel"),agentRoot=by("agentpanel"),hint=by("modelshint");
 if(!policyRoot||!agentRoot)return;
 if(hint)hint.textContent="Загрузка настроек…";
 const results=await Promise.allSettled([renderModelPolicy(policyRoot),renderAgentSettings(agentRoot)]);
 if(hint)hint.textContent=results.some(r=>r.status==="rejected")?"Часть настроек недоступна. Обновите приложение.":"Настройки редакции и управляющего агента";
}
function modelName(id){
 return String(id||"").replace(/^[a-z]+\//,"").replace(/-/g," ").replace(/\b\w/g,c=>c.toUpperCase());
}
function money2(value){
 const n=Number(value);
 if(!Number.isFinite(n))return "—";
 return new Intl.NumberFormat("ru-RU",{maximumFractionDigits:4}).format(n)+" ₽";
}
async function renderModelPolicy(root){
 const e=coreEl;
 const d=await coreRequest("/api/core/models");
 root.replaceChildren();
 const head=e("div",null,root);head.className="costrow";
 e("h3","Политика редакции · ревизия "+d.requested.revision,head);
 e("p",d.pendingSync||d.executorSync!=="acknowledged"?"Исполнитель ещё не подтвердил эту ревизию.":"Исполнитель работает на подтверждённой ревизии "+d.effective.revision+".",head);
 const note=e("p",null,root);note.className="muted";
 note.textContent="Действующий маршрут исполнителя: RouterAI. Claude через Claudy — подготовленный маршрут, платное исполнение пока отключено. Уже созданные заказы сохраняют свою ревизию.";
 const providers=new Map((d.providerPlan||[]).map(m=>[m.id,m]));
 const roles=e("div",null,root);roles.className="core-row";
 for(const role of d.roles){
  const block=e("article",null,roles);block.className="mode-panel";
  e("h2",role.role,block);
  const current=d.requested.models[role.id];
  const line=e("p",null,block);line.className="muted";
  line.textContent="Текущая настройка: "+modelName(current)+" · "+(d.requested.reasoning[role.id]||"off")+" · действующий маршрут RouterAI";
  const target=providers.get(current);
  if(target?.targetProvider==="claudy")e("p","Целевой маршрут: Claudy · платное исполнение отключено",block).className="muted";
  const pick=e("select",null,block);pick.className="review-input";pick.setAttribute("aria-label","Модель роли "+role.role);
  const catalogue=new Map(d.catalogue.map(m=>[m.id,m]));
  for(const id of role.candidates){
   const model=catalogue.get(id);
   const option=e("option",null,pick);
   option.value=id;
   const planned=providers.get(id);
   option.textContent=modelName(id)+" · RouterAI"+(planned?.targetProvider==="claudy"?" → Claudy (план)":"")+(model?.priceVerified?"":" · тариф RouterAI не подтверждён");
   option.disabled=id!==current&&(!model||model.available!==true||model.priceVerified!==true);
   if(id===current)option.selected=true;
  }
  const level=e("select",null,block);level.className="review-input";level.setAttribute("aria-label","Уровень мышления роли "+role.role);
  if(role.capability==="image"){
   const option=e("option",null,level);option.value="off";option.textContent="Мышление не применяется";option.selected=true;level.disabled=true;
  }else{
   for(const value of d.reasoningLevels){
    const option=e("option",null,level);option.value=value;option.textContent="Мышление: "+value;
    if(value===(d.requested.reasoning[role.id]||"off"))option.selected=true;
   }
  }
  const apply=e("button","Сохранить роль",block);apply.className="review-action";
  apply.addEventListener("click",async()=>{
   const models={...d.requested.models},reasoning={...d.requested.reasoning};
   models[role.id]=pick.value;reasoning[role.id]=level.value;
   if(!window.confirm("Сохранить выбранную модель и уровень мышления для новых заказов?"))return;
   apply.disabled=true;
   try{
    await coreRequest("/api/core/models/settings",{confirm:true,revision:d.requested.revision,action:"set",models,reasoning});
    await renderModelPolicy(root);
   }catch(err){apply.disabled=false;window.alert(err.message);}
  });
 }
 const prices=e("div",null,root);prices.className="core-row";
 const priceBlock=e("article",null,prices);priceBlock.className="mode-panel";
 e("h2","Тарифы действующего маршрута RouterAI",priceBlock);
 for(const model of d.catalogue){
  const line=e("p",null,priceBlock);line.className="muted";
  if(!model.priceVerified){
   line.textContent=modelName(model.id)+": цена не подтверждена — модель недоступна для выбора.";
   continue;
  }
  const pricing=model.pricing||{};
  const parts=[];
  if(pricing.prompt)parts.push("ввод "+money2(Number(pricing.prompt.value)*1000)+"/тыс. токенов");
  if(pricing.completion)parts.push("вывод "+money2(Number(pricing.completion.value)*1000)+"/тыс. токенов");
  if(pricing.image_output)parts.push("изображение "+money2(pricing.image_output.value));
  line.textContent=modelName(model.id)+": "+(parts.join(" · ")||"тариф не указан");
 }
 const future=e("article",null,prices);future.className="mode-panel";
 e("h2","Claudy · целевой маршрут",future);
 e("p","Расчётные тарифы адаптера, не подтверждение фактического списания. Платный запуск ещё отключён.",future).className="muted";
 for(const p of providers.values())if(p.targetProvider==="claudy"){
  const rate=p.rateStatus==="configured_estimate"?"ввод "+money2(p.inputRubPerMillion)+" / млн · вывод "+money2(p.outputRubPerMillion)+" / млн":"тариф и маршрут не настроены";
  e("p",modelName(p.id)+": "+rate,future).className="muted";
 }
 const history=e("div",null,root);history.className="core-row";
 const historyBlock=e("article",null,history);historyBlock.className="mode-panel";
 e("h2","История ревизий",historyBlock);
 if(!d.history.length){const empty=e("p","Изменений ещё не было.",historyBlock);empty.className="muted";}
 for(const entry of d.history){
  const line=e("p",null,historyBlock);line.className="muted";
  const models=Object.keys(entry.models).map(id=>id+" → "+modelName(entry.models[id])).join(", ");
  const levels=Object.keys(entry.reasoning||{}).map(id=>id+" "+entry.reasoning[id]).join(", ");
  line.textContent="Ревизия "+entry.revision+": "+models+" (мышление: "+levels+")";
 }
}
async function renderAgentSettings(root){
 const e=coreEl;
 const d=await coreRequest("/api/core/agent/settings");
 const s=d.settings;
 root.replaceChildren();
 const block=e("div",null,root);block.className="mode-panel";
 e("h2","Управляющий агент",block);
 const summary=e("p",null,block);summary.className="muted";
 summary.textContent="Модель: "+modelName(s.model)+" · мышление: "+s.reasoning+" · за запрос: "+money2(s.perCallRub)+" · за сутки: "+money2(s.dailyRub);
 e("p","Действующий маршрут: RouterAI. Переход Claude на Claudy пока не активирован.",block).className="muted";
 const modelPick=e("select",null,block);modelPick.className="review-input";modelPick.setAttribute("aria-label","Модель управляющего агента");
 for(const model of d.models){
  const option=e("option",null,modelPick);option.value=model.id;option.textContent=model.label;
  if(model.id===s.model)option.selected=true;
 }
 const levelPick=e("select",null,block);levelPick.className="review-input";levelPick.setAttribute("aria-label","Уровень мышления агента");
 for(const value of d.reasoningLevels){
  const option=e("option",null,levelPick);option.value=value;option.textContent="Мышление: "+value;
  if(value===s.reasoning)option.selected=true;
 }
 const perCall=e("input",null,block);perCall.className="review-input";perCall.type="number";perCall.step="0.5";perCall.min="0.5";
 perCall.value=String(s.perCallRub);perCall.setAttribute("aria-label","Лимит на один запрос");
 const daily=e("input",null,block);daily.className="review-input";daily.type="number";daily.step="1";daily.min="1";
 daily.value=String(s.dailyRub);daily.setAttribute("aria-label","Лимит на сутки");
 const opus=e("input",null,block);opus.className="review-input";opus.type="number";opus.step="0.01";opus.min="0";
 opus.value=String(s.opusDailyRub??0);opus.setAttribute("aria-label","Лимит Opus на сутки");
 const labels=e("p",null,block);labels.className="muted";
 labels.textContent="Лимиты агента в ₽: на запрос, на сутки и на Opus в пределах суточного лимита агента.";
 const save=e("button","Сохранить настройки агента",block);save.className="review-action";
 save.addEventListener("click",async()=>{
  const body={confirm:true,version:s.version,model:modelPick.value,reasoning:levelPick.value,
   perCallRub:Number(perCall.value),dailyRub:Number(daily.value),opusDailyRub:Number(opus.value)};
  if(!window.confirm("Сменить модель управляющего агента и его лимиты? История и память сохранятся."))return;
  save.disabled=true;
  try{await coreRequest("/api/core/agent/settings/write",body);await renderAgentSettings(root);await loadAgent();}
  catch(err){save.disabled=false;window.alert(err.message);}
 });
 const hist=e("div",null,root);hist.className="core-row";
 const histBlock=e("article",null,hist);histBlock.className="mode-panel";
 e("h2","История настроек агента",histBlock);
 if(!d.history.length){const empty=e("p","Изменений ещё не было.",histBlock);empty.className="muted";}
 for(const entry of d.history){
  const line=e("p",null,histBlock);line.className="muted";
  line.textContent="Ревизия "+entry.version+": "+modelName(entry.model)+" · мышление "+entry.reasoning+" · "+money2(entry.perCallRub)+" за запрос, "+money2(entry.dailyRub)+" за сутки";
 }
}
`;
