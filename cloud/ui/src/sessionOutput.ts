import { agentIdOf, type SessionEvent } from "./api";
export interface OutputRow {id:number;agentId:string;timestamp:number;text:string;error:boolean}
export function sessionOutput(events:readonly SessionEvent[],turnId:string|null,maxEvent:number,live:boolean):OutputRow[] {
 const rows:OutputRow[]=[];
 for(const event of events) {
  const agentId=agentIdOf(event)??"primary";
  if(!live && (agentId!=="primary" || event.id>maxEvent))continue;
  const data=event.data as Record<string,unknown>;
  if(agentId==="primary" && turnId && typeof data.turn_id==="string" && data.turn_id!==turnId)continue;
  let text="",error=false;
  if(event.type==="tool_call")text=typeof data.command==="string"?data.command:"Tool started";
  else if(event.type==="tool_result") {text=typeof data.output==="string"?data.output:"";error=data.is_error===true || (typeof data.returncode==="number" && data.returncode!==0); if(!text)text=error?"Command failed":"Command finished";}
  else if(event.type==="system_note")text=typeof data.content==="string"?data.content:typeof data.text==="string"?data.text:"";
  else if(event.type==="lifecycle")text=typeof data.status==="string"?data.status:"";
  else if(event.type==="agent_error") {text=typeof data.error==="string"?data.error:"Agent error";error=true;}
  if(text)rows.push({id:event.id,agentId,timestamp:event.timestamp,text,error});
 }
 return rows;
}
