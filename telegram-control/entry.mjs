import { DurableObject } from "cloudflare:workers";
import worker from "./worker.mjs";
import {CoreHandler} from "./core.mjs";
import {ReviewJournal as JournalHandler} from "./journal.mjs";
export class ReviewJournal extends DurableObject {
 constructor(ctx,env){super(ctx,env);this.handler=new JournalHandler(ctx);this.core=new CoreHandler(ctx);}
 async fetch(request){return new URL(request.url).pathname.startsWith("/core")?this.core.fetch(request):this.handler.fetch(request);}
 async alarm(){return this.core.alarm();}
}
export default worker;
