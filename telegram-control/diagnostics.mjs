// Deterministic read-only diagnostics. Reports are observations, never proof of a root cause.
// No retries, model calls, scheduler changes, budget changes, or release authorization.
const FRESH=300000;
const RULES={
 emergency_paused:["info","Аварийная пауза включена","Оставить остановку до решения владельца."],
 production_locked:["info","Производство отключено до запуска","Проверить готовность исполнителя; не включать автоматически."],
 mode_not_effective:["info","Выбранный режим ещё не действует","Завершить интеграцию исполнителя."],
 schedule_paused:["info","Расписание на паузе","Включать только по подтверждению владельца."],
 alarm_missing:["warning","У активного тестового расписания отсутствует таймер","Проверить локальный таймер; восстановление отдельно."],
 alarm_overdue:["warning","Таймер тестового расписания просрочен","Проверить состояние планировщика; не повторять выполненные задания."],
 config_unverified:["warning","Конфигурация проекта недоступна","Повторить только чтение конфигурации."],
 budget_mismatch:["critical","Лимиты управления и конфигурации различаются","Сверить область действия лимитов, не увеличивать бюджет автоматически."],
 ledger_disconnected:["warning","Общий журнал списаний не подтверждён","Не разрешать платное исполнение."],
 preview_missing:["info","Тестовый план ещё не сформирован","Сформировать план после подтверждения владельца."],
 receipt_missing:["warning","Нет отчёта исполнителя","Проверить приёмку и связь; не отправлять задание повторно вслепую."],
 receipt_stale:["warning","Отчёт исполнителя устарел","Запросить свежий снимок только на чтение."],
 charge_unsettled:["critical","Снимок содержит несверенное списание","Сверить фактическое списание; не повторять платный запрос."],
 main_exhausted:["warning","Основной бюджет исчерпан по снимку","Не начинать новые статьи; проверить допустимость резерва."],
 reserve_exhausted:["critical","Резерв исчерпан по снимку","Остановить дополнительные расходы до сверки."],
 stage_failed:["warning","Исполнитель сообщил об ошибке этапа","Проверить исходный отчёт, причину и состояние расходов перед исправлением."],
 stage_blocked:["warning","Исполнитель сообщил о блокировке этапа","Проверить зависимость и причину блокировки; не обходить проверки."],
 chat_limit:["info","Учтённые расходы или число запросов чата достигли лимита","Не выполнять новые платные запросы чата."],
 chat_charge_unknown:["critical","Стоимость запроса чата не подтверждена","Сверить списание; не повторять автоматически."]
};
export function diagnose({ops,settings,alarmAt,next=[],budget,jobs=[],readiness=null,messages=[]},now=Date.now()){
 const findings=[];
 const add=(code,scope="system",evidence={})=>{const [severity,title,action]=RULES[code];findings.push({code,severity,title,action,scope,evidence});};
 add("production_locked");
 if(ops?.stopped)add("emergency_paused");
 if(ops?.requestedMode&&ops.requestedMode!=="manual")add("mode_not_effective","system",{requestedMode:ops.requestedMode});
 if(settings?.paused)add("schedule_paused");
 else if(!ops?.stopped&&next.length){
  if(alarmAt==null)add("alarm_missing");
  else if(Number.isFinite(alarmAt)&&alarmAt<now-FRESH)add("alarm_overdue","system",{alarmAt});
 }
 if(readiness!==null){
  if(readiness.verified!==true)add("config_unverified");
  else if(Number.isFinite(readiness.configuredDailyRub)&&budget?.policy&&Math.round(readiness.configuredDailyRub*100)!==budget.policy.mainKop)add("budget_mismatch","system",{configuredDailyRub:readiness.configuredDailyRub,ownerDailyRub:budget.policy.mainKop/100});
 }
 if(budget?.production?.ledgerConnected!==true)add("ledger_disconnected");
 if(budget?.chat&&(budget.chat.accountedRub>=budget.chat.limitRub||budget.chat.calls>=20))add("chat_limit");
 for(const m of messages){if(m?.status==="failed"&&m.cost==null)add("chat_charge_unknown","chat",{id:m.id});}
 for(const j of jobs){
  if(j.status==="cancelled")continue;
  if(!j.preview){add("preview_missing",j.id);continue;}
  const r=j.executorReceipt;
  if(!r){add("receipt_missing",j.id);continue;}
  const stale=now-r.observedAt>FRESH;
  if(stale)add("receipt_stale",j.id,{observedAt:r.observedAt});
  const day=new Date(now+10800000).toISOString().slice(0,10);
  const currentDay=r.budget.day===day;
  if(r.budget.unsettledRequest)add("charge_unsettled",j.id,{observedAt:r.observedAt,stale});
  if(currentDay&&Number(r.budget.mainRemainingRub)===0)add("main_exhausted",j.id,{observedAt:r.observedAt,stale});
  if(currentDay&&Number(r.budget.reserveRemainingRub)===0)add("reserve_exhausted",j.id,{observedAt:r.observedAt,stale});
  for(const a of r.articles)for(const s of a.stages){
   if(s.status==="failed"||s.status==="blocked")add(s.status==="failed"?"stage_failed":"stage_blocked",j.id,{articleId:a.id,stage:s.id,observedAt:r.observedAt,stale});
  }
 }
 const rank={critical:0,warning:1,info:2};
 findings.sort((a,b)=>rank[a.severity]-rank[b.severity]);
 return {mode:"read_only",checkedAt:new Date(now).toISOString(),findings:findings.slice(0,80),total:findings.length,productionEnabled:false,publicationAuthorized:false,automaticRecoveryEnabled:false};
}
