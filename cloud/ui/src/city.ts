import { idOf, type ParticleField } from "./graph";
import type { Node3D, Scene3D } from "./graph3d";

export const CITY_VERSION = 4;
export const ARCHETYPES = ["slab", "monolith", "stepped tower", "twin block", "central spine", "podium tower"] as const;
export interface CityPlot extends Node3D {
  archetype: number; width: number; height: number; depth: number; terrace: number; slot: number;
  site: string;
}
export interface District {
  id: string; name: string; x: number; z: number; width: number; depth: number; terrace: number;
  capacity: number; slots: {x: number; z: number}[];
}
export interface CityLayout extends Scene3D {
  nodes: CityPlot[]; byId: Map<string, CityPlot>; districts: District[]; assignments: Map<string, CityPlot>;
  districtSlots: Map<string, number>; version: typeof CITY_VERSION;
}
export function pathHash(path: string): number {
  let n = 2166136261;
  for (const c of path) n = Math.imul(n ^ c.charCodeAt(0), 16777619);
  return n >>> 0;
}
// Ten scene units equal one architectural unit.
export function dimensions(bytes: number) {
  const s = Math.min(1, Math.log2(1 + Math.max(0, Number.isFinite(bytes) ? bytes : 0)) / 16);
  return {width: 6.5 + s * 4.5, depth: 6.5 + s * 4, height: 12 + Math.pow(s, 1.8) * 68};
}
function plots(count: number) {
  const out: {x:number; z:number}[]=[];
  // Orthogonal blocks surround a pair of open pedestrian axes.
  for(let ring=0;out.length<count;ring++) {
    const row:{x:number;z:number}[]=[];
    for(let x=-ring;x<=ring;x++) for(let z=-ring;z<=ring;z++) {
      if(Math.max(Math.abs(x),Math.abs(z))!==ring)continue;
      if(ring>1 && x===0 && z%4===0)continue;
      row.push({x:x*10.5+(x>0?1:0),z:z*10.5+(z>0?1:0)});
    }
    row.sort((a,b)=>Math.hypot(a.x,a.z)-Math.hypot(b.x,b.z)||a.x-b.x||a.z-b.z);
    out.push(...row);
  }
  return out.slice(0,count);
}
/* The platform is a plinth, not a plinth-stack: three thin steps carry the
   district's colour while the buildings do the silhouette. */
export const TERRACE_LEVELS=[{scale:1.14,y:0},{scale:.96,y:.55},{scale:.66,y:1.1}] as const;
export function plotElevation(x:number,z:number,width:number,depth:number) {
  const extent=Math.max((Math.abs(x)+6)/(width/2),(Math.abs(z)+6)/(depth/2));
  return extent<.52?1.75:extent<.75?1.2:.65;
}
function newSite(name: string, count: number, districts: District[]): District {
  const capacity = Math.max(4, Math.ceil(count * 1.12 / 4) * 4);
  const slots = plots(capacity);
  const radius = Math.max(...slots.map(p => Math.max(Math.abs(p.x),Math.abs(p.z)))) + 8;
  // Allocate against retained site bounds. New overflow sites cannot rearrange or intersect old ones.
  let x = 0, z = 0;
  for (let i = 0; ; i++) {
    const a = i * 2.399963229728653;
    const distance = Math.sqrt(i) * 18;
    x = Math.cos(a) * distance; z = Math.sin(a) * distance;
    if (districts.every(d => Math.hypot(x-d.x-d.width/2,z-d.z-d.depth/2) > radius*1.16+d.width*.58+12)) break;
  }
  return {id: `${name}::${districts.filter(d => d.name === name).length}`, name,
    x:x-radius, z:z-radius, width:radius*2, depth:radius*2, terrace:5, capacity, slots};
}
export function buildCity(field: ParticleField, previous?: CityLayout | null): CityLayout {
  const prior = previous?.version === CITY_VERSION && previous.districts.length ? previous : null;
  const assignments = new Map(prior?.assignments);
  const districtSlots = new Map(prior?.districtSlots);
  const districts = (prior?.districts ?? []).map(d => ({...d}));
  const nodes: CityPlot[] = [];
  const groups = [...new Set(field.particles.map(p => p.cluster))].sort((a,b)=>field.particles.filter(p=>p.cluster===b).length-field.particles.filter(p=>p.cluster===a).length||a.localeCompare(b));
  if (!prior) {
    // Compose initial districts in camera-aligned shelves. Choose the aspect
    // closest to the desktop viewport; later edits never repack these sites.
    for (const name of groups) districts.push(newSite(name,field.particles.filter(p=>p.cluster===name).length,districts));
    const reference=[[-.55,0],[.08,-.72],[.68,0],[-.42,.74],[.58,.84],[1.02,-.68]];
    const span=Math.max(...districts.map(d=>d.width))*.68;
    const centers: {u:number;v:number;r:number}[]=[];
    districts.forEach((d,i)=>{
      const anchor=reference[i]??[Math.cos(i*2.4)*1.05,Math.sin(i*2.4)*1.05];
      const c={u:anchor[0]*span,v:anchor[1]*span,r:d.width*.62};
      for(let pass=0;pass<32;pass++) for(const other of centers) {
        const dx=c.u-other.u,dz=c.v-other.v,distance=Math.hypot(dx,dz),needed=c.r+other.r+4;
        if(distance<needed){c.u+=dx/Math.max(1,distance)*(needed-distance);c.v+=dz/Math.max(1,distance)*(needed-distance);}
      }
      centers.push(c);d.x=(c.u*1.22+c.v)*Math.SQRT1_2-d.width/2;d.z=(c.v-c.u*1.22)*Math.SQRT1_2-d.depth/2;
    });
  }
  for (const name of groups) {
    if (!districtSlots.has(name)) districtSlots.set(name,districtSlots.size);
    const files = field.particles.filter(p => p.cluster === name).sort((a,b) => b.size-a.size || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
    for (const p of files) {
      const old = assignments.get(p.id);
      let site = old ? districts.find(d => d.id === old.site) : undefined;
      let slot = old?.slot ?? 0;
      if (!site) {
        site = districts.find(d => d.name === name && [...assignments.values()].filter(p => p.site === d.id).length < d.capacity);
        if (!site) { site = newSite(name, files.filter(f => !assignments.has(f.id)).length, districts); districts.push(site); }
        slot = Math.max(-1,...[...assignments.values()].filter(p => p.site === site!.id).map(p => p.slot))+1;
      }
      const at = site.slots[slot];
      const dim = dimensions(p.size);
      const archetype = pathHash(p.path) % 6;
      const centrality = 1 - Math.min(1,Math.hypot(at.x,at.z)/(site.width/2));
      const plot: CityPlot = {id:p.id, cluster:name, hue:p.hue, r:6, index:nodes.length,
        x:site.x+site.width/2+at.x, z:site.z+site.depth/2+at.z, y:plotElevation(at.x,at.z,site.width,site.depth),
        terrace:5, site:site.id, slot, archetype, ...dim,
        height: Math.min(78,Math.max(5, dim.height * (0.55+centrality*.65) * [0.6,1,1,.95,1,1.15][archetype]))};
      assignments.set(p.id,plot); nodes.push(plot);
    }
  }
  const byId = new Map(nodes.map(n => [n.id,n]));
  const links = field.filaments.filter(l => byId.has(idOf(l.source)) && byId.has(idOf(l.target))).map(l => ({...l,source:idOf(l.source),target:idOf(l.target),across:false,samples:2,dashed:l.kind === "cotouch"}));
  return {version:CITY_VERSION,nodes,byId,links,districts,assignments,districtSlots,anchors:new Map(),waists:new Map(),edgeVertices:links.length*2,signature:`city-v3:${field.signature}`};
}

// Separate, bounded cache: never reads or writes particle coordinates.
const cities = new Map<string, CityLayout>();
export function recallCity(session: string | null) {
  return session ? cities.get(session) : undefined;
}
export function rememberCity(session: string | null, city: CityLayout) {
  if (!session) return;
  cities.delete(session);
  cities.set(session, city);
  while (cities.size > 4) cities.delete(cities.keys().next().value!);
}
