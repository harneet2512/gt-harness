import { idOf, type ParticleField } from "./graph";
import type { Node3D, Scene3D } from "./graph3d";

export const CITY_VERSION = 5;
export const ARCHETYPES = ["slab", "monolith", "stepped tower", "twin block", "central spine", "podium tower"] as const;
export interface CityPlot extends Node3D {
  archetype: number; width: number; height: number; depth: number; terrace: number; slot: number;
  site: string;
  /** Weighted dependency degree, 0..1 within this city — feeds height. */
  centrality: number;
  /** A file the whole repo leans on — it gets the lit roof. */
  landmark: boolean;
}
export interface District {
  id: string; name: string; x: number; z: number; width: number; depth: number; terrace: number;
  capacity: number; slots: {x: number; z: number}[];
  /** Total dependency strength to other districts — drives its route and gravity. */
  weight: number;
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
    x:x-radius, z:z-radius, width:radius*2, depth:radius*2, terrace:5, capacity, slots, weight: 0};
}
export function buildCity(field: ParticleField, previous?: CityLayout | null): CityLayout {
  const prior = previous?.version === CITY_VERSION && previous.districts.length ? previous : null;
  const assignments = new Map(prior?.assignments);
  const districtSlots = new Map(prior?.districtSlots);
  const districts = (prior?.districts ?? []).map(d => ({...d}));
  const nodes: CityPlot[] = [];

  /* --- architecture before geometry ------------------------------------
     The field is repository truth: real files, real imports. Districts are
     discovered, not assumed — and a dominant top-level dir in a big repo
     splits into its second level so the skyline reflects real structure. */
  const dirCounts = new Map<string, number>();
  for (const p of field.particles) dirCounts.set(p.cluster, (dirCounts.get(p.cluster) ?? 0) + 1);
  const splitAt = field.particles.length > 500 ? 80 : 140;
  const clusterOf = (p: (typeof field.particles)[number]) => {
    if ((dirCounts.get(p.cluster) ?? 0) <= splitAt) return p.cluster;
    const seg = p.path.replace(/\/$/, "").split("/");
    return seg.length > 2 ? `${p.cluster}/${seg[1]}` : p.cluster;
  };

  /* Dependency truth: per-file centrality and district-to-district weight. */
  const centrality = new Map<string, number>();
  const pairWeight = new Map<string, number>();
  const idCluster = new Map<string, string>();
  for (const p of field.particles) idCluster.set(p.id, clusterOf(p));
  for (const l of field.filaments) {
    const s = idOf(l.source), t = idOf(l.target);
    const w = (l as { weight?: number }).weight ?? 1;
    centrality.set(s, (centrality.get(s) ?? 0) + w);
    centrality.set(t, (centrality.get(t) ?? 0) + w);
    const ca = idCluster.get(s), cb = idCluster.get(t);
    if (ca && cb && ca !== cb) {
      const key = ca < cb ? `${ca}${cb}` : `${cb}${ca}`;
      pairWeight.set(key, (pairWeight.get(key) ?? 0) + w);
    }
  }
  const maxCent = Math.max(1, ...centrality.values());

  const groups = [...new Set(field.particles.map(clusterOf))].sort((a,b)=>field.particles.filter(p=>clusterOf(p)===b).length-field.particles.filter(p=>clusterOf(p)===a).length||a.localeCompare(b));
  if (!prior) {
    /* Placement is affinity-ordered: walk the dependency matrix like a
       chain — each district goes next to its strongest related neighbor —
       then lay the chain on a ring around the hub and resolve collisions. */
    const affinity = (a: string, b: string) => pairWeight.get(a < b ? `${a}${b}` : `${b}${a}`) ?? 0;
    const order: string[] = groups.length ? [groups[0]] : [];
    const rest = new Set(groups.slice(1));
    while (rest.size) {
      let best = "", bw = -1;
      for (const g of rest) {
        const w = order.reduce((s, o) => s + affinity(o, g), 0);
        if (w > bw || (w === bw && g < best)) { bw = w; best = g; }
      }
      order.push(best); rest.delete(best);
    }
    for (const name of order) districts.push(newSite(name,field.particles.filter(p=>clusterOf(p)===name).length,districts));
    /* The island, not the archipelago: districts pull close enough that
       the gaps between them read as avenues, not straits. */
    const span=Math.max(...districts.map(d=>d.width))*.44;
    const centers: {u:number;v:number;r:number}[]=[];
    const byName = new Map(districts.map(d=>[d.name,d]));
    order.forEach((name,i)=>{
      const d=byName.get(name)!;
      const a=(i/Math.max(1,order.length))*Math.PI*2-Math.PI/2;
      const c={u:Math.cos(a)*span,v:Math.sin(a)*span*.82,r:d.width*.62};
      for(let pass=0;pass<32;pass++) for(const other of centers) {
        const dx=c.u-other.u,dz=c.v-other.v,distance=Math.hypot(dx,dz),needed=c.r+other.r+9;
        if(distance<needed){c.u+=dx/Math.max(1,distance)*(needed-distance);c.v+=dz/Math.max(1,distance)*(needed-distance);}
      }
      centers.push(c);d.x=(c.u*1.22+c.v)*Math.SQRT1_2-d.width/2;d.z=(c.v-c.u*1.22)*Math.SQRT1_2-d.depth/2;
    });
  }
  for (const name of groups) {
    if (!districtSlots.has(name)) districtSlots.set(name,districtSlots.size);
    const files = field.particles.filter(p => clusterOf(p) === name).sort((a,b) => b.size-a.size || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
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
      /* Geometry is metric truth: footprint from bytes, height from bytes
         and dependency centrality — a file the whole repo leans on towers
         without a skyscraper outlier breaking the skyline. */
      const cent = Math.min(1, (centrality.get(p.id) ?? 0) / maxCent);
      const positional = 1 - Math.min(1,Math.hypot(at.x,at.z)/(site.width/2));
      const plot: CityPlot = {id:p.id, cluster:name, hue:p.hue, r:6, index:nodes.length,
        x:site.x+site.width/2+at.x, z:site.z+site.depth/2+at.z, y:2.4,
        terrace:5, site:site.id, slot, archetype, ...dim,
        centrality: cent, landmark: cent > 0.62 && dim.height > 24,
        height: Math.min(78,Math.max(5, dim.height * (0.4+positional*.3+cent*.55) * [0.38,1.4,1,0.8,1,1.28][archetype]))};
      assignments.set(p.id,plot); nodes.push(plot);
    }
  }
  /* District weight = its share of inter-district dependency — it drives
     how strongly the hub pulls a route to it. */
  for (const d of districts) d.weight = 0;
  for (const [key, w] of pairWeight) {
    const sep = key.indexOf("");
    const a = key.slice(0, sep), b = key.slice(sep + 1);
    for (const d of districts) if (d.name === a || d.name === b) d.weight += w;
  }
  const byId = new Map(nodes.map(n => [n.id,n]));
  const links = field.filaments.filter(l => byId.has(idOf(l.source)) && byId.has(idOf(l.target))).map(l => ({...l,source:idOf(l.source),target:idOf(l.target),across:false,samples:2,dashed:l.kind === "cotouch"}));
  return {version:CITY_VERSION,nodes,byId,links,districts,assignments,districtSlots,anchors:new Map(),waists:new Map(),edgeVertices:links.length*2,signature:`city-v5:${field.signature}`};
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
