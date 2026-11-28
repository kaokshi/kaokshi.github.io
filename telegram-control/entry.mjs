import { DurableObject } from "cloudflare:workers";
import worker from "./worker.mjs";
import {CoreHandler} from "./core.mjs";
import {AgentHandler} from "./agent.mjs";
import {ReviewJournal as JournalHandler} from "./journal.mjs";
import {AnalyticsHandler} from "./studio-analytics.mjs";
export class ReviewJournal extends DurableObject {
 constructor(ctx,env){super(ctx,env);this.handler=new JournalHandler(ctx);this.core=new CoreHandler(ctx,env);this.agent=new AgentHandler(ctx,env,this.core);this.analytics=new AnalyticsHandler(ctx,env);}
 async fetch(request){const p=new URL(request.url).pathname;return p.startsWith("/analytics/")?this.analytics.fetch(request):["/core/agent","/core/agent/chat","/core/agent/apply","/core/agent/reconcile"].includes(p)?this.agent.fetch(request):p.startsWith("/core")?this.core.fetch(request):this.handler.fetch(request);}
 async alarm(){return this.core.alarm();}
}
export default worker;
