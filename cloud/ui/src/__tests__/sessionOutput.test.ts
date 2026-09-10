import {expect,it} from "vitest";
import {sessionOutput} from "../sessionOutput";
import {parseEventFrame} from "../api";
const events=[
 {id:1,timestamp:100,type:"tool_call",data:{turn_id:"t",command:"cat a.ts"}},
 {id:2,timestamp:101,type:"tool_result",data:{turn_id:"t",output:"a",returncode:0}},
 {id:3,timestamp:102,type:"tool_result",data:{agent_id:"w",turn_id:"other",output:"worker",returncode:1}},
 {id:4,timestamp:103,type:"tool_result",data:{turn_id:"future",output:"future",returncode:0}},
].map(e=>parseEventFrame(JSON.stringify(e))!);
it("preserves real timestamps, worker identity and return-code failures",()=>{
 expect(sessionOutput(events,"t",Infinity,true)).toEqual([
 {id:1,timestamp:100,agentId:"primary",text:"cat a.ts",error:false},
 {id:2,timestamp:101,agentId:"primary",text:"a",error:false},
 {id:3,timestamp:102,agentId:"w",text:"worker",error:true},
 ]);
});
it("does not leak independent worker or future turn output into replay",()=>{
 expect(sessionOutput(events,"t",2,false).map(r=>r.id)).toEqual([1,2]);
 expect(sessionOutput(events,"t",0,false)).toEqual([]);
});
