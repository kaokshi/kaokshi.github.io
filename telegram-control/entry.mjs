import { DurableObject } from "cloudflare:workers";
import worker from "./worker.mjs";
import {ReviewJournal as JournalHandler} from "./journal.mjs";
export class ReviewJournal extends DurableObject {
 constructor(ctx,env){super(ctx,env);this.handler=new JournalHandler(ctx);}
 async fetch(request){return this.handler.fetch(request);}
}
export default worker;
