import { useState } from "react";
import { Link } from "react-router-dom";
import type { AgentVisualState } from "../cityAgents";
import type { GraphView } from "../useGraphView";
import type { SessionData } from "../useSessionData";
import CommandOutput from "./CommandOutput";
import DiffView from "./DiffView";
import { Icon, SurveyorMark } from "./WorkspaceChrome";
import SurveyorPortrait from "./SurveyorPortrait";
interface Props {
 agent:AgentVisualState|null; agents:readonly AgentVisualState[]; view:GraphView; data:SessionData;
 followedAgent:string|null; onSelectAgent:(id:string)=>void; onClose:()=>void; onFollow:(id:string)=>void; onPick:(path:string)=>void;
}
export default function LiveAgentInspector({agent,agents,view,data,followedAgent,onSelectAgent,onClose,onFollow,onPick}:Props) {
 const [tab,setTab]=useState("Overview");
 const worker=agent && agent.id!=="primary"?data.chat.workers.byId[agent.id]:null;
 const path=agent?.fileId?view.field.byId.get(agent.fileId)?.path:null;
 const rows=agent?.id==="primary"?view.steps.slice(0,view.cutoff):worker?.activity??[];
 const changes=agent?.id==="primary"?[...view.editedAtCutoff.keys()]:agent?.changes??[];
 const mayShowDiff=agent?.id==="primary"||worker?.status==="applied";
 return <aside className="live-inspector" aria-label={agent?"Agent inspector":"Workspace inspector"}>
   <header className="live-inspector-heading"><Icon name="graph"/><span>{agent?"Agent":"Workspace"}</span>{agent&&<button aria-label="Close agent inspector" onClick={onClose}>×</button>}</header>
   {!agent ? <div className="workspace-overview"><span className="eyebrow">Repository intelligence</span><h2>Your agents at work.</h2><p>Select an agent to follow its activity, or a building to inspect the file and its changes.</p>
    <div className="workspace-overview-agents">{agents.map(a=><button key={a.id} onClick={()=>onSelectAgent(a.id)}><SurveyorMark color={a.color}/><span>{a.label}<small>{a.activity}</small></span><span>↗</span></button>)}</div>
    <dl><div><dt>Session</dt><dd>{data.session?.status??data.phase??"Connecting"}</dd></div><div><dt>GroundTruth</dt><dd>{data.session?.gt_status??"pending"}</dd></div><div><dt>Changed files</dt><dd>{view.editedAtCutoff.size}</dd></div><div><dt>Receipts</dt><dd>{data.receipts.length}</dd></div></dl>
    {data.loadError&&<p role="alert">{data.loadError}</p>}{data.gtError&&<p>{data.gtError}</p>}
   </div> : <>
    <div className="agent-profile"><div><h2><i style={{background:agent.color}}/>{agent.label}</h2><p style={{color:agent.color}}>{agent.activity}</p><small>{agent.location==="unknown"?"Location unknown":`${agent.location} file location`}</small></div><SurveyorPortrait color={agent.color}/></div>
    <nav className="agent-inspector-tabs" aria-label="Agent details">{["Overview","Files","Tools","Logs"].map(t=><button key={t} aria-pressed={tab===t} onClick={()=>setTab(t)}>{t}</button>)}</nav>
    <div className="agent-inspector-content">
     {tab==="Overview" && <>
      <h3>Current file</h3>{path?<button className="current-file-card" onClick={()=>onPick(path)}><span className="file-language">{view.field.byId.get(agent.fileId!)?.lang||"file"}</span><span>{path}<small>{path.slice(0,path.lastIndexOf("/"))}</small></span></button>:<p className="ins-empty">{agent.activity==="outside"?"Working outside this repository.":"Waiting for a file location from agent activity."}</p>}
      <h3>Task</h3><p>{worker?.task || data.session?.last_message || "Ready for your next instruction."}</p>
      <div className="agent-metrics"><div><strong>{rows.length}</strong><small>Tool activities</small></div><div><strong>{changes.length}</strong><small>Changed files</small></div></div>
      <h3>Recent activity</h3><ol className="agent-activity-list">{rows.slice(-6).map(row=><li key={row.key}><Icon name="clock" size={13}/><span>{row.command || "Tool activity"}</span>{row.returncode!==null&&<small>{row.isError?"Error":"Finished"}</small>}</li>)}</ol>{rows.length===0&&<p className="ins-empty">No tool activity yet.</p>}
     </>}
     {tab==="Files" && <><h3>Reported changes</h3>{changes.map(p=><button className="agent-file-row" key={p} onClick={()=>onPick(p)}><Icon name="file"/>{p}</button>)}{!changes.length&&<p>No changes reported.</p>}</>}
     {tab==="Tools" && <><h3>Latest command</h3><pre>{agent.command||"No command yet."}</pre><CommandOutput output={agent.output} returncode={agent.returncode} isError={agent.isError}/></>}
     {tab==="Logs" && <>{rows.map(row=><section key={row.key}><pre>{row.command}</pre><CommandOutput output={row.output} returncode={row.returncode} isError={row.isError}/></section>)}{!rows.length&&<p>No retained activity at this cutoff.</p>}</>}
    </div>
    <div className="agent-inspector-actions"><button aria-pressed={followedAgent===agent.id} onClick={()=>onFollow(agent.id)}><Icon name="target"/>{followedAgent===agent.id?"Stop following":"Follow agent"}</button>{worker && <Link to={`/sessions/${worker.id}`}>Open session ↗</Link>}
      {worker?.status==="reported"&&!worker.isExternal&&<button disabled={data.session?.status!=="idle"||worker.applying} onClick={()=>void data.applyWorker(worker.id)}>{worker.applying?"Applying…":"Apply changes"}</button>}
      {worker?.applyError&&<p role="alert">{worker.applyError}</p>}
    </div>
    {path&&mayShowDiff&&<section className="agent-live-diff"><h3>Diff <span>{path}</span></h3><DiffView path={path} diff={view.diffAtCutoff} file={view.editedAtCutoff.get(path)} note={view.diffNote} loading={data.diffLoading} error={data.diffError}/></section>}
   </>}
 </aside>;
}
