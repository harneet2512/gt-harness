import { Link } from "react-router-dom";
import type { AgentVisualState } from "../cityAgents";
import type { Session } from "../api";
import { repoShort } from "../format";

export type WorkspaceView = "Structure" | "Flow" | "Agents";
export function Icon({name, size=17}:{name:string;size?:number}) {
  const paths:Record<string,string>={
    home:"m3 10 9-7 9 7v10H3Z M9 20v-7h6v7", graph:"M6 6h12M6 6v12m0 0h12M6 6l12 12", file:"M6 3h8l4 4v14H6Z M14 3v5h5", branch:"M7 4v16M7 15c0-7 10-2 10-10", chat:"M3 4h18v13H8l-5 4Z", settings:"M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1M5.6 18.4l2.1-2.1m8.6-8.6 2.1-2.1 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0", plus:"M12 5v14M5 12h14", search:"M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14m5 12 6 6", layers:"m12 3 10 6-10 6L2 9Zm-9 11 9 6 9-6", log:"m4 5 5 6-5 6m9 0h7", sun:"M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0", moon:"M20 15A9 9 0 0 1 9 3a9 9 0 1 0 11 12", target:"M12 2v4m0 12v4M2 12h4m12 0h4M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0", clock:"M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"};
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]??paths.graph}/></svg>;
}
export function SurveyorMark({color="#3182f6",large=false}:{color?:string;large?:boolean}) {
  return <svg className={large?"surveyor-mark is-large":"surveyor-mark"} viewBox="0 0 90 52" fill="none" aria-hidden="true">
    <path d="m28 20-17-5m50 5 17-5M30 28l-18 9m48-9 18 9" stroke="currentColor" strokeWidth="4"/>
    {[[13,14],[77,14],[13,37],[77,37]].map(([x,y])=><g key={`${x}-${y}`}><ellipse cx={x} cy={y} rx="11" ry="4" stroke="currentColor" strokeWidth="2.5"/><path d={`M${x-7} ${y}h14`} stroke="currentColor"/></g>)}
    <path d="M30 20q15-10 30 0l-3 11H33Z" fill="currentColor" opacity=".8"/><path d="m37 31 3 9h10l3-9" fill="currentColor"/><ellipse cx="45" cy="40" rx="4" ry="2" fill="#16212f"/><circle cx="45" cy="23" r="2.5" fill={color}/>
  </svg>;
}
export default function WorkspaceNav({session,agents,selectedAgent,onAgent,onView,onConversation,onSessions,onSettings,onOutput,view}:{session:Session|null;agents:readonly AgentVisualState[];selectedAgent:string|null;onAgent:(id:string)=>void;onView:(v:WorkspaceView)=>void;onConversation:()=>void;onSessions:()=>void;onSettings:()=>void;onOutput:(tab:"changes"|"receipts"|"trail")=>void;view:WorkspaceView}) {
  return <aside className="workspace-nav" aria-label="Workspace navigation">
    <Link className="workspace-brand" to="/"><b>GT</b><span>Cloud Agent</span></Link>
    <button className="workspace-repo" onClick={onSessions}><Icon name="file"/><span>{session?repoShort(session.repo):"Sessions"}<small><Icon name="branch" size={12}/>{session?.ref??"Choose a repository"}</small></span><span>↗</span></button>
    <nav className="workspace-links">
      <button onClick={onSessions}><Icon name="home"/>Overview</button>
      <button className="is-active" onClick={()=>onView("Structure")}><Icon name="graph"/>Live<span className="nav-count">{agents.filter(a=>a.running).length}</span></button>
      <button onClick={onConversation}><Icon name="chat"/>Conversation</button>
      <button onClick={()=>onOutput("changes")}><Icon name="branch"/>Changes</button>
      <button onClick={()=>onOutput("receipts")}><Icon name="clock"/>Receipts</button>
      <button onClick={onSettings}><Icon name="settings"/>Settings</button>
    </nav>
    <div className="nav-section-title">Agents <button aria-label="Manage agents in conversation" onClick={onConversation}><Icon name="plus" size={15}/></button></div>
    <div className="workspace-agents">{agents.map(a=><button key={a.id} className={selectedAgent===a.id?"is-active":""} onClick={()=>onAgent(a.id)}><SurveyorMark color={a.color}/><span><strong><i style={{background:a.color}}/>{a.label}</strong><small>{a.activity === "outside" ? "Outside repository" : a.activity}</small></span></button>)}</div>
    <div className="nav-section-title">Views</div>
    <nav className="workspace-links">
      <button className={view==="Structure"?"is-active":""} onClick={()=>onView("Structure")}><Icon name="layers"/>Repository</button>
      <button className={view==="Flow"?"is-active":""} onClick={()=>onView("Flow")}><Icon name="graph"/>Dependencies</button>
      <button className={view==="Agents"?"is-active":""} onClick={()=>onView("Agents")}><Icon name="target"/>Agent activity</button>
      <button onClick={()=>onOutput("trail")}><Icon name="log"/>Execution trail</button>
    </nav>
    <div className="workspace-session-status"><i className={session?.status==="running"?"is-running":""}/><span>{session?.status??"Connecting"}<small>{session?.gt_status === "ready"?"GroundTruth available":`GroundTruth ${session?.gt_status??"pending"}`}</small></span></div>
  </aside>;
}
