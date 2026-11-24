// Owner-only, read-only Telegram control interface. No generation or publishing.
const MENU = {keyboard:[[{text:"Состояние редакции"},{text:"Баланс RouterAI"}],[{text:"Модели"},{text:"Тесты"}],[{text:"Статьи"},{text:"Помощь"}]],resize_keyboard:true};
const COMMANDS = new Map([
 ["/start","menu"],["/help","menu"],["Помощь","menu"],["/status","status"],["Состояние редакции","status"],
 ["/balance","balance"],["Баланс RouterAI","balance"],["/models","models"],["Модели","models"],
 ["/tests","tests"],["Тесты","tests"],["/articles","articles"],["Статьи","articles"],["/expenses","expenses"]
]);
const REPO = "kaokshi/kaokshi.github.io";
const PRIVATE = "kaokshi/zen-editorial-review";
const MODELS = "Планируемая связка (не подтверждение доступности):\nQwen 3.6 Plus — управление\nClaude Sonnet 5.5 — автор\nDeepSeek V4 Pro — редактор\nSeedream 5.0 Flash — иллюстрации.";
function configured(env) {
 return Boolean(env.TELEGRAM_BOT_TOKEN && /^[0-9]+$/.test(env.TELEGRAM_OWNER_ID || "") && /^[A-Za-z0-9_-]{32,256}$/.test(env.TELEGRAM_WEBHOOK_SECRET || ""));
}
export function authorized(message, env) {
 return configured(env) && message?.chat?.type === "private" &&
 String(message?.from?.id) === env.TELEGRAM_OWNER_ID &&
 String(message?.chat?.id) === env.TELEGRAM_OWNER_ID &&
 message.from.is_bot === false;
}
async function getJson(url, token, extra={}) {
 const response=await fetch(url,{headers:{Authorization:"Bearer "+token,Accept:"application/json",...extra},redirect:"manual",signal:AbortSignal.timeout(8000)});
 if(!response.ok) throw Error("upstream_unavailable");
 return response.json();
}
async function github(env,path) {
 if(!env.EDITORIAL_PRIVATE_TOKEN) throw Error("missing_github_token");
 return getJson("https://api.github.com/"+path,env.EDITORIAL_PRIVATE_TOKEN,{"User-Agent":"ZenEditorialBot","X-GitHub-Api-Version":"2022-11-28"});
}
async function balance(env) {
 if(!env.ROUTERAI_API_KEY) return "Ключ RouterAI не настроен в Worker.";
 const res=await getJson("https://routerai.ru/api/v1/credits",env.ROUTERAI_API_KEY);
 const data=res.data;
 const raw=data?.credits ?? data?.total_credits;
 if(raw===null || raw===undefined || raw==="" || !["number","string"].includes(typeof raw) || !Number.isFinite(Number(raw))) return "Формат баланса не распознан. Проверь кабинет RouterAI.";
 return "Баланс RouterAI по API: "+Number(raw).toFixed(2)+" ₽. Данные могут обновляться с задержкой.";
}
async function articleReport(env) {
 const info=await github(env,"repos/"+PRIVATE);
 if(info.full_name!==PRIVATE || info.private!==true) throw Error("private_repo_not_verified");
 const rows=await github(env,"repos/"+PRIVATE+"/contents/routerai-validation");
 if(!Array.isArray(rows)) throw Error("unexpected_review_directory");
 const dirs=rows.filter(x=>x.type==="dir" && /^[A-Za-z0-9_-]{1,100}$/.test(x.name)).sort((a,b)=>b.name.localeCompare(a.name)).slice(0,8);
 return dirs.length?"Каталоги тестовых материалов (не подтверждение готовности):\n"+dirs.map(x=>x.name+" — https://github.com/"+PRIVATE+"/tree/"+encodeURIComponent(info.default_branch)+"/routerai-validation/"+encodeURIComponent(x.name)).join("\n"):"Каталоги тестовых материалов пока отсутствуют.";
}
export async function command(text,env) {
 switch(COMMANDS.get(text.trim())) {
 case "menu":return "Дзен — ИИ-редакция\nВыберите раздел. Режим только просмотра: генерация и публикация недоступны.";
 case "balance":return balance(env);
 case "models":return MODELS;
 case "status":return "Бот работает в режиме просмотра. Производственные флаги и состояние задач пока не синхронизированы.";
 case "articles":case "tests":return articleReport(env);
 case "expenses":return "Подтверждённый журнал расходов пока не подключён. Используйте /balance.";
 default:return "Команда не поддерживается. Используйте /help.";
 }
}
export default {async fetch(request,env) {
 const url=new URL(request.url);
 if(request.method==="GET" && url.pathname==="/health") return new Response("ok");
 if(request.method!=="POST" || url.pathname!=="/telegram") return new Response("Not found",{status:404});
 if(!configured(env)) return new Response("Not configured",{status:503});
 if(request.headers.get("X-Telegram-Bot-Api-Secret-Token")!==env.TELEGRAM_WEBHOOK_SECRET) return new Response("Forbidden",{status:403});
 let update;
 try {const body=await request.text();if(body.length>65536)return new Response("Too large",{status:413});update=JSON.parse(body);}catch{return new Response("Bad request",{status:400});}
 const message=update?.message;
 if(!authorized(message,env)||typeof message.text!=="string") return new Response("ok");
 let reply;
 try{reply=await command(message.text,env);}catch{reply="Не удалось получить данные. Проверь настройки подключения. Платные запросы не выполнялись.";}
 try{
 const sent=await fetch("https://api.telegram.org/bot"+env.TELEGRAM_BOT_TOKEN+"/sendMessage",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:env.TELEGRAM_OWNER_ID,text:reply.slice(0,3900),reply_markup:MENU,link_preview_options:{is_disabled:true}}),redirect:"manual",signal:AbortSignal.timeout(10000)});
 if(!sent.ok)return new Response("Delivery failed",{status:502});
 const receipt=await sent.json();
 if(receipt.ok!==true)return new Response("Delivery failed",{status:502});
 }catch{return new Response("Delivery failed",{status:502});}
 return new Response("ok");
}};
