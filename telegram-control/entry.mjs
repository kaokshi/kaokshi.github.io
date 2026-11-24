import { DurableObject } from "cloudflare:workers";
import worker from "./worker.mjs";
import {CoreHandler} from "./core.mjs";
import {AgentHandler} from "./agent.mjs";
import {ReviewJournal as JournalHandler} from "./journal.mjs";
export class ReviewJournal extends DurableObject {
 constructor(ctx,env){super(ctx,env);this.handler=new JournalHandler(ctx);this.core=new CoreHandler(ctx,env);this.agent=new AgentHandler(ctx,env,this.core);}
 async fetch(request){const p=new URL(request.url).pathname;return p.startsWith("/core/agent")?this.agent.fetch(request):p.startsWith("/core")?this.core.fetch(request):this.handler.fetch(request);}
 async alarm(){return this.core.alarm();}
}
export default worker;
