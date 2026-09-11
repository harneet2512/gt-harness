import {
  BoxGeometry,
  ACESFilmicToneMapping,
  SRGBColorSpace,
  DoubleSide,
  BufferGeometry,
  Color,
  CanvasTexture,
  ConeGeometry,
  DirectionalLight,
  Group,
  HemisphereLight,
  InstancedMesh,
  LineBasicMaterial,
  GridHelper,
  Line,
  LineSegments,
  Float32BufferAttribute,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Object3D,
  PerspectiveCamera,
  PlaneGeometry,
  PCFShadowMap,
  Raycaster,
  Scene,
  Fog,
  TorusGeometry,
  CylinderGeometry,
  TubeGeometry,
  QuadraticBezierCurve3,
  RingGeometry,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import type { GraphViewProps } from "../graphProps";
import { pathHash, TERRACE_LEVELS, type CityLayout, type CityPlot, type District } from "../city";
import type { Node3D } from "../graph3d";
import { palette } from "../palette";
import { CameraMotion, TIMING, travel } from "../cityMotion";
import { occupancy, type AgentVisualState } from "../cityAgents";
import { buildingGeometry, terraceGeometry, roofFootprint, facadeTexture, litFacadeTexture, treeGeometry } from "./cityGeometry";
import { SurveyorFactory } from "./surveyor";

export interface Frame3DState extends GraphViewProps {
  hoverId: string | null;
  reduced: boolean;
}
export interface Renderer3DOptions {
  canvas: HTMLCanvasElement;
  overlay: HTMLCanvasElement;
  getState: () => Frame3DState;
  onZoom?: (k: number) => void;
}
interface Vehicle {
  group: Group;
  scan: Mesh;
  from: Vector3;
  to: Vector3;
  start: number;
  duration: number;
  event: string | null;
  accent: Mesh;
  accentMaterial: MeshBasicMaterial;
  activity: AgentVisualState["activity"];
  enteredAt: number;
  trail: Line;
}

/** Imperative city renderer. One scheduler owns controls, camera, vehicles and theme. */
export class Renderer3D {
  private gl: WebGLRenderer;
  private scene = new Scene();
  private camera = new PerspectiveCamera(38, 1, 0.1, 20000);
  private overviewDistance = 200;
  private aoCamera=new PerspectiveCamera();
  private keyLight = new DirectionalLight(0xffffff, 2.2);
  private controls: OrbitControls;
  private composer: EffectComposer;
  private ao: GTAOPass;
  private renderPass: RenderPass;
  private outputPass: OutputPass;
  private floorMaterial=new MeshStandardMaterial({color:0xf7f8fa,roughness:1,metalness:0});
  private floorGeometry=new PlaneGeometry(8000,8000);
  private siteLinesMaterial=new LineBasicMaterial({color:0x8995a4,transparent:true,opacity:.055,depthWrite:false});
  private walkwayMaterial=new MeshStandardMaterial({color:0xdfe4ea,roughness:.75,metalness:.05,emissive:new Color(0x8fb8d8),emissiveIntensity:.55});
  private siteMarkTexture:CanvasTexture;
  private siteMarkMaterial:MeshBasicMaterial;
  private siteMarkGeometry=new PlaneGeometry(30,15);
  /* The warm pool of light the city stands on — a radial wash, paper to
     cream, that makes the composition read as one staged city. */
  private stageTexture:CanvasTexture = (() => {
    const c = document.createElement("canvas");
    c.width = 256; c.height = 256;
    const t = new CanvasTexture(c);
    const g = c.getContext("2d");
    if (!g) return t;
    const grad = g.createRadialGradient(128, 128, 10, 128, 128, 128);
    grad.addColorStop(0, "rgba(240,225,200,0.9)");
    grad.addColorStop(0.55, "rgba(235,222,205,0.42)");
    grad.addColorStop(1, "rgba(235,222,205,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 256, 256);
    t.needsUpdate = true;
    return t;
  })();
  private layout: CityLayout | null = null;
  private buildings: InstancedMesh[] = [];
  private ids: string[][] = [];
  private terrain = new Group();
  private solids = new MeshStandardMaterial({
    roughness: 0.82,
    metalness: 0.05,
    vertexColors: true,
    map: facadeTexture(),
    emissiveMap: litFacadeTexture(),
    emissive: new Color(0xffc890),
    emissiveIntensity: 0.55,
  });
  private ground = new MeshStandardMaterial({
    roughness: 0.9,
    metalness: 0,
    vertexColors: true,
  });
  /* The skirts under a district — rock, not more terrace, so each district
     reads as a landmass rather than a slab on a table. */
  private rock = new MeshStandardMaterial({
    color: 0x878e9a,
    roughness: 1,
    metalness: 0,
    vertexColors: true,
  });
  private leaf = new MeshStandardMaterial({
    color: 0x8fae86,
    roughness: 1,
    metalness: 0,
  });
  /* A district is a neighborhood: it needs its own color. The hue is a
     stable function of the district's identity — never its name — so the
     same repo colors itself identically every time and no repository
     concept is hardcoded. */
  private districtTint(name: string): Color {
    const palette = ["#7ba7d9", "#b394dd", "#85c4a2", "#dd92a6", "#6fbccb",
      "#ddb36b", "#9e93d8", "#8fb4d9", "#c9a88a", "#7fc8b4"];
    return new Color(palette[pathHash(name) % palette.length]);
  }
  private contact = new MeshBasicMaterial({
    color: 0x171b20,
    transparent: true,
    opacity: 0.045,
    depthWrite: false,
  });
  private geometry = Array.from({ length: 6 }, (_, i) => buildingGeometry(i));
  private tree = treeGeometry();
  private terraces: BufferGeometry[] = [];
  private fittedDistance = 300;
  private box = new BoxGeometry(1, 1, 1);
  private scanGeometry = new ConeGeometry(1, 1, 24, 1, true);
  private scanMaterial = new MeshBasicMaterial({
    color: 0x78afbd,
    transparent: true,
    opacity: 0.08,
    side: DoubleSide,
    depthWrite: false,
  });
  private factory = new SurveyorFactory();
  private vehicles = new Map<string, Vehicle>();
  private ray = new Raycaster();
  private pointer = new Vector2();
  private matrix = new Object3D();
  private vector = new Vector3();
  private cameraMotion = new CameraMotion();
  private targetMotion = new CameraMotion();
  private width = 1;
  private height = 1;
  private raf: number | null = null;
  private disposed = false;
  private framed = false;
  private userOwned = false;
  private reduced = false;
  private selected: string | null = null;
  private followed: string | null = null;
  private followOffset = new Vector3(65, 75, 90);
  private skin: ReturnType<typeof palette> | null = null;
  private themeAt = 0;
  private themeFrom = [new Color(), new Color(), new Color()];
  private themeTo = [new Color(), new Color(), new Color()];
  private edges: LineSegments;
  private edgeKey = "";
  private colorsKey = "";
  private colorAt = 0;
  private colorFrom: Color[][] = [];
  private colorTo: Color[][] = [];
  private lastFrame = 0;
  private slowFrames = 0;
  private ratio = 1;
  private replayToken = "";
  private input = false;
  private animationNow = 0;
  private labelLimit = 12;
  /* The entrance: buildings rise from their plots when they are new. A first
     city rises wholesale; a file the agent adds later rises alone. */
  private riseAt = -1;
  private riseDelays: number[][] = [];
  private risePlots: CityPlot[][] = [];
  private risenIds = new Set<string>();
  private rising = false;
  constructor(private options: Renderer3DOptions) {
    this.gl = new WebGLRenderer({
      canvas: options.canvas,
      antialias: true,
      depth: true,
      alpha: false,
      stencil: false,
    });
    this.gl.outputColorSpace = SRGBColorSpace;
    this.gl.toneMapping = ACESFilmicToneMapping;
    this.gl.toneMappingExposure = 1.1;
    this.scene.background = new Color("#f7f8fa");
    this.scene.add(new HemisphereLight(0xfff4e2, 0xa1a9b5, 1.35));
    const key = this.keyLight;
    key.color.set(0xffe8c4);
    key.intensity = 1.55;
    key.position.set(-100, 220, 100);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, {
      left: -180,
      right: 180,
      top: 180,
      bottom: -180,
      near: 1,
      far: 500,
    });
    key.shadow.bias = -0.00015;
    key.shadow.normalBias = 0.35;
    key.shadow.radius = 2.5;
    key.shadow.intensity = 0.55;
    this.gl.shadowMap.enabled = true;
    this.gl.shadowMap.type = PCFShadowMap;
    this.scene.add(key, key.target);
    const fill = new DirectionalLight(0xe3eafa, 0.6);
    fill.position.set(150, 100, -90);
    this.scene.add(fill);
    const floor=new Mesh(this.floorGeometry,this.floorMaterial);
    floor.rotation.x=-Math.PI/2;floor.position.y=-.35;floor.receiveShadow=true;
    this.scene.add(floor);
    /* The drafting table: a faint engineering grid under the whole city —
       the architectural-model signal that makes paper read as a surface. */
    const grid = new GridHelper(2400, 240, 0xd8dde4, 0xe8ebef);
    (grid.material as LineBasicMaterial).transparent = true;
    (grid.material as LineBasicMaterial).opacity = 0.55;
    grid.position.y = -0.3;
    this.scene.add(grid);
    const mark=document.createElement("canvas");mark.width=256;mark.height=128;
    const ink=mark.getContext("2d")!;ink.fillStyle="#ffffff";ink.font="650 100px Arial";ink.textAlign="center";ink.fillText("GT",128,99);
    this.siteMarkTexture=new CanvasTexture(mark);
    this.siteMarkMaterial=new MeshBasicMaterial({map:this.siteMarkTexture,color:0x798698,transparent:true,opacity:.38,depthWrite:false});
    this.composer=new EffectComposer(this.gl);
    this.composer.renderTarget1.samples=4;this.composer.renderTarget2.samples=4;
    this.renderPass=new RenderPass(this.scene,this.camera);
    this.camera.layers.enable(1);
    this.ao=new GTAOPass(this.scene,this.aoCamera,1,1);
    this.ao.blendIntensity=.65;
    this.ao.updateGtaoMaterial({radius:9,distanceExponent:1,thickness:2,scale:1});
    this.outputPass=new OutputPass();
    this.composer.addPass(this.renderPass);this.composer.addPass(this.ao);this.composer.addPass(this.outputPass);
    this.scene.add(this.terrain);
    this.edges = new LineSegments(
      new BufferGeometry(),
      new LineBasicMaterial({
        transparent: true,
        opacity: 0.9,
        depthTest: true,
        vertexColors: true,
      }),
    );
    this.scene.add(this.edges);
    this.controls = new OrbitControls(this.camera, options.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.autoRotate = false;
    this.controls.minDistance = 35;
    this.controls.maxDistance = 12000;
    this.controls.zoomSpeed = .8;
    this.controls.minPolarAngle = Math.PI / 6;
    this.controls.maxPolarAngle = (73 * Math.PI) / 180;
    this.controls.addEventListener("start", this.onStart);
    this.controls.addEventListener("end", this.onEnd);
    this.controls.addEventListener("change", this.kick);
  }
  private onStart = () => {
    this.input = true;
    this.userOwned = true;
    this.cameraMotion.cancel();
    this.targetMotion.cancel();
  };
  private onEnd = () => {
    this.input = false;
    if (this.followed) {
      const v = this.vehicles.get(this.followed);
      if (v) this.followOffset.copy(this.camera.position).sub(v.group.position);
    }
    this.kick();
  };
  setReduced(reduced: boolean) {
    this.reduced = reduced;
    this.controls.enableDamping = !reduced;
    this.kick();
  }
  resize(w: number, h: number, dpr: number) {
    if (w < 2 || h < 2) return;
    this.width = w;
    this.height = h;
    this.ratio = Math.min(2, dpr);
    this.gl.setPixelRatio(this.ratio);
    this.gl.setSize(w, h, false);
    this.composer.setPixelRatio(this.ratio);
    this.composer.setSize(w,h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this.framed || !this.userOwned) this.fit();
    this.kick();
  }
  setLayout(layout: CityLayout, _carried: boolean) {
    for (const [, pu] of this.pulses) {
      this.scene.remove(pu.mesh);
      pu.mat.dispose();
    }
    this.pulses.clear();
    this.buildings.forEach((m) => {
      this.scene.remove(m);
      m.dispose();
    });
    this.buildings = [];
    this.ids = [];
    this.terrain.traverse((o) => {
      if (o instanceof InstancedMesh) o.dispose();
    });
    this.terrain.clear();
    this.terraces.forEach(g => g.dispose());
    this.terraces = [];
    this.layout = layout;
    const districts=layout.districts;
    // A city we have never framed is a new city: its whole skyline is new
    // construction. A carried one keeps whatever already stands.
    if (!this.framed) this.risenIds.clear();
    const cx = districts.length
      ? districts.reduce((s, d) => s + d.x + d.width / 2, 0) / districts.length
      : 0;
    const cz = districts.length
      ? districts.reduce((s, d) => s + d.z + d.depth / 2, 0) / districts.length
      : 0;
    if(districts.length) {
      const left=Math.min(...districts.map(d=>d.x))-100,right=Math.max(...districts.map(d=>d.x+d.width))+100;
      const back=Math.min(...districts.map(d=>d.z))-100,front=Math.max(...districts.map(d=>d.z+d.depth))+100;
      const step=Math.max(20,(right-left)/60,(front-back)/60),lines:number[]=[];
      for(let x=Math.floor(left/step)*step;x<right;x+=step)lines.push(x,-.29,back,x,-.29,front);
      for(let z=Math.floor(back/step)*step;z<front;z+=step)lines.push(left,-.29,z,right,-.29,z);
      const grid=new BufferGeometry();grid.setAttribute("position",new Float32BufferAttribute(lines,3));this.terraces.push(grid);
      this.terrain.add(new LineSegments(grid,this.siteLinesMaterial));
      const mark=new Mesh(this.siteMarkGeometry,this.siteMarkMaterial);mark.rotation.x=-Math.PI/2;mark.rotation.z=Math.PI/4;mark.layers.set(1);
      mark.position.set((left+right)/2,-.27,(back+front)/2);this.terrain.add(mark);
      // Walkways belong to the architectural site; graph relations remain separate.
      const connected=new Set([0]);
      while(connected.size<districts.length) {
        let best:{a:number;b:number;distance:number}|null=null;
        for(const a of connected)for(let b=0;b<districts.length;b++) {
          if(connected.has(b))continue;
          const x= districtCenter(districts[a]),z=districtCenter(districts[b]),distance=x.distanceTo(z);
          if(!best||distance<best.distance)best={a,b,distance};
        }
        if(!best)break;
        const a=districts[best.a],b=districts[best.b],start=districtCenter(a),end=districtCenter(b),direction=end.clone().sub(start).normalize();
        start.addScaledVector(direction,a.width*.48);end.addScaledVector(direction,-b.width*.48);
        const bridge=new Mesh(this.box,this.walkwayMaterial);bridge.position.copy(start).add(end).multiplyScalar(.5);bridge.position.y=4.7;
        bridge.scale.set(3.5,.65,start.distanceTo(end));bridge.rotation.y=Math.atan2(direction.x,direction.z);bridge.castShadow=true;bridge.receiveShadow=true;this.terrain.add(bridge);
        connected.add(best.b);
      }
    }
    for (let kind = 0; kind < 6; kind++) {
      const plots = layout.nodes.filter((p) => p.archetype === kind);
      /* Each archetype gets its own facade variant — same shader, same
         state, a different window rhythm — so the city reads as six
         neighborhoods of architecture, not one stamp repeated. */
      const mat = this.solids.clone();
      mat.map = facadeTexture(kind % 3);
      mat.emissiveMap = litFacadeTexture(kind * 31 + 7);
      const mesh = new InstancedMesh(
        this.geometry[kind],
        mat,
        plots.length,
      );
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.ids.push(plots.map((p) => p.id));
      this.risePlots[kind] = plots;
      this.riseDelays[kind] = plots.map((p) => {
        if (this.risenIds.has(p.id) || this.options.getState().reduced) return -1;
        this.risenIds.add(p.id);
        // The wave travels out from the city's middle: nearer plots first.
        const dx = p.x - cx,
          dz = p.z - cz;
        return 120 + Math.min(560, Math.hypot(dx, dz) * 1.1);
      });
      const fresh = this.riseDelays[kind].some((d) => d >= 0);
      const startHidden = fresh && !this.options.getState().reduced;
      plots.forEach((p, i) => {
        const delay = this.riseDelays[kind][i];
        const hidden = startHidden && delay >= 0;
        this.matrix.position.set(p.x, p.y, p.z);
        /* A quarter-turn by identity: the same archetype reads differently
           on different plots, doubling the skyline's variety for free. */
        this.matrix.rotation.set(0, ((pathHash(p.id) >> 4) % 4) * Math.PI / 2, 0);
        this.matrix.scale.set(
          p.width * (hidden ? 0.82 : 1),
          hidden ? 0.001 : p.height,
          p.depth * (hidden ? 0.82 : 1),
        );
        this.matrix.updateMatrix();
        mesh.setMatrixAt(i, this.matrix.matrix);
        mesh.setColorAt(i, new Color(0xffffff));
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
      this.buildings.push(mesh);
      this.scene.add(mesh);
    }
    if (this.riseDelays.some((k) => k.some((d) => d >= 0))) {
      this.riseAt = this.animationNow;
      this.rising = true;
    }
    for (const d of layout.districts) {
      const geometry = terraceGeometry(pathHash(d.name) % 37);
      this.terraces.push(geometry);
      const tint = this.districtTint(d.name);
      const slab = this.ground.clone();
      slab.color.copy(tint).lerp(new Color(0xffffff), 0.55);
      const rock = this.rock.clone();
      rock.color.copy(tint).multiplyScalar(0.75);
      const footing=new Mesh(geometry,this.contact);
      footing.layers.set(1);
      footing.position.set(d.x+d.width/2,-.15,d.z+d.depth/2);
      footing.scale.set(d.width*1.18,.02,d.depth*1.18);
      this.terrain.add(footing);
      for (let layer = 0; layer < TERRACE_LEVELS.length; layer++) {
        const m = new Mesh(geometry, slab);
        m.receiveShadow = true; m.castShadow = true;
        m.position.set(d.x + d.width / 2, TERRACE_LEVELS[layer].y, d.z + d.depth / 2);
        const expansion = TERRACE_LEVELS[layer].scale;
        /* The extrusion is 1.4 deep; squeezing it to a plate is what makes
           the district a platform instead of a cuboid. */
        m.scale.set(d.width * expansion, 0.32, d.depth * expansion);
        this.terrain.add(m);
      }
      /* A thin shadowed skirt, not a landmass: just enough to give the
         platform an edge and a shadow side. The district's mass belongs
         to the buildings on it, not the ground under it. */
      const cliff=new Mesh(geometry,rock);
      cliff.position.set(d.x+d.width/2,0,d.z+d.depth/2);
      cliff.scale.set(d.width*1.14,3.2,d.depth*1.14);
      cliff.castShadow=true;
      this.terrain.add(cliff);
      // Planting: a loose ring of trees around the district's mid-terrace.
      const seed=pathHash(d.id);
      const trees=new InstancedMesh(this.tree,this.leaf,26);
      for(let i=0;i<26;i++) {
        /* Two plantings: a promenade at the platform rim, and a street
           row down the pedestrian axis the plots deliberately leave
           open — green where the buildings aren't. */
        const street = i >= 16;
        const a=(i/(street?10:16))*Math.PI*2+((seed%97)/97)*Math.PI*2;
        const wobble=((seed+i*31)%100)/100;
        const tx=street
          ? d.x+d.width/2+((i%2)?2.2:-2.2)
          : d.x+d.width/2+Math.cos(a)*d.width*(.44+.05*wobble);
        const tz=street
          ? d.z+d.depth*.15+wobble*d.depth*.7
          : d.z+d.depth/2+Math.sin(a)*d.depth*(.44+.05*wobble);
        const s=(street?3.4:4.5)+((seed+i*13)%100)/100*3;
        this.matrix.position.set(tx, street ? 1.2 : .5, tz);
        this.matrix.scale.set(s,s,s);
        this.matrix.rotation.set(0,a,0);
        this.matrix.updateMatrix();
        trees.setMatrixAt(i,this.matrix.matrix);
      }
      trees.castShadow=true;
      this.terrain.add(trees);
    }
    if(districts.length>1){
      /* The stage: a warm pool of light under the whole city, so the
         composition reads staged rather than scattered on paper. */
      const stage = new Mesh(
        new PlaneGeometry(1, 1),
        new MeshBasicMaterial({ map: this.stageTexture, transparent: true, opacity: 0.85, depthWrite: false }),
      );
      stage.rotation.x = -Math.PI / 2;
      stage.position.set(cx, -0.4, cz);
      const stageR = Math.max(
        ...districts.map((d) => Math.hypot(d.x + d.width / 2 - cx, d.z + d.depth / 2 - cz) + Math.max(d.width, d.depth) * 0.75),
      );
      stage.scale.set(stageR * 2.6, stageR * 2.6, 1);
      stage.renderOrder = -1;
      this.terrain.add(stage);
      /* The center is the repository's root — ontologically it's where
         every district's route terminates, not a monument. A flat forum:
         nothing stands here, everything passes through. */
      const forum=new Mesh(
        new CylinderGeometry(16,16.8,0.5,48),
        new MeshStandardMaterial({color:0xeef0f3,roughness:.9,metalness:0}),
      );
      forum.position.set(cx,0.25,cz);
      forum.receiveShadow=true;
      this.terrain.add(forum);
      this.hub.set(cx, 6, cz);
      /* A flush inlay ring — pavement detail, not a tower. */
      const inlay=new Mesh(
        new TorusGeometry(10,0.35,8,64),
        new MeshStandardMaterial({color:0xd6dbe2,roughness:.8,metalness:0}),
      );
      inlay.rotation.x=Math.PI/2;
      inlay.position.set(cx,0.52,cz);
      this.terrain.add(inlay);
      /* Routes are dependency truth: a ground path from the hub to each
         district, its girth proportional to real inter-district weight.
         Weak districts get a thread; load-bearing ones get a boulevard. */
      const maxW = Math.max(1, ...districts.map((d) => d.weight));
      for (const d of districts) {
        const dx = d.x + d.width / 2, dz = d.z + d.depth / 2;
        const w = d.weight / maxW;
        const r = 0.6 + w * 2.2;
        const mid = new Vector3((cx + dx) / 2, 0.3, (cz + dz) / 2);
        const curve = new QuadraticBezierCurve3(
          new Vector3(cx, 0.3, cz), mid, new Vector3(dx, 0.3, dz));
        const tube = new Mesh(
          new TubeGeometry(curve, 20, r, 5, false),
          this.walkwayMaterial,
        );
        tube.receiveShadow = true;
        this.terrain.add(tube);
      }
    }
    const contacts = new InstancedMesh(
      this.box,
      this.contact,
      layout.nodes.length,
    );
    contacts.layers.set(1);
    layout.nodes.forEach((p, i) => {
      this.matrix.position.set(p.x, p.y + 0.03, p.z);
      this.matrix.scale.set(p.width + 3, 0.02, p.depth + 3);
      this.matrix.updateMatrix();
      contacts.setMatrixAt(i, this.matrix.matrix);
    });
    this.terrain.add(contacts);
    /* Landmarks: the files the whole repository leans on get a lit roof —
       cheap geometry, real hierarchy. */
    const landmarks = layout.nodes.filter((p) => p.landmark);
    if (landmarks.length) {
      const caps = new InstancedMesh(
        this.box,
        new MeshStandardMaterial({
          color: 0xf5e8c8, roughness: 0.4, metalness: 0.1,
          emissive: new Color(0xffb45e), emissiveIntensity: 1.4,
        }),
        landmarks.length,
      );
      landmarks.forEach((p, i) => {
        this.matrix.position.set(p.x, p.y + p.height + 0.4, p.z);
        this.matrix.scale.set(p.width * 0.6, 0.5, p.depth * 0.6);
        this.matrix.rotation.set(0, 0, 0);
        this.matrix.updateMatrix();
        caps.setMatrixAt(i, this.matrix.matrix);
      });
      this.terrain.add(caps);
    }
    this.edgeKey = "";
    this.colorsKey = "";
    this.colorFrom = this.ids.map((ids) => ids.map(() => new Color()));
    this.colorTo = this.ids.map((ids) => ids.map(() => new Color()));
    if (!this.framed) this.fit();
    this.kick();
  }
  private frame(target: Vector3, distance: number, duration: number) {
    const direction = this.camera.position.clone().sub(this.controls.target);
    if (direction.length() < 1) direction.set(1, 1.3, 1.4);
    direction.normalize().multiplyScalar(distance);
    const now = this.lastFrame ? this.animationNow : performance.now();
    this.targetMotion.begin(
      this.controls.target,
      target,
      now,
      duration,
      this.reduced,
    );
    this.cameraMotion.begin(
      this.camera.position,
      target.clone().add(direction),
      now,
      duration,
      this.reduced,
    );
    this.kick();
  }
  zoomBy(factor: number) {
    if (!Number.isFinite(factor) || factor <= 0) return;
    this.onStart();
    const distance = Math.max(this.controls.minDistance, Math.min(this.controls.maxDistance,
      this.camera.position.distanceTo(this.controls.target) / factor));
    if (this.followed) this.followOffset.copy(this.camera.position).sub(this.controls.target).setLength(distance);
    this.input = false;
    this.frame(this.controls.target.clone(), distance, TIMING.panel);
  }
  private hub = new Vector3(0, 6, 0);
  /* The living layer: one expanding ring per touch, colour by what the
     touch was. Born once per event id, dead in under two seconds. */
  private pulses = new Map<string, { mesh: Mesh; mat: MeshBasicMaterial; start: number }>();
  private pulseGeometry = new RingGeometry(1, 1.4, 28);
  private pulseTones: Record<string, number> = {
    read: 0x6fb0d8,
    edit: 0xe0a055,
    fail: 0xd06868,
    note: 0x9a93d8,
  };
  private dockPosition(slot:number) {
    /* Idle agents hold station above the forum — the repository root is
       where work converges, so that's where an idle drone belongs. */
    const a = slot * 1.7 + 0.6;
    const r = 26 + (slot % 3) * 8;
    return new Vector3(this.hub.x + Math.cos(a) * r, this.hub.y + 12 + (slot % 2) * 7, this.hub.z + Math.sin(a) * r);
  }
  fit() {
    this.userOwned = false;
    if (!this.layout?.nodes.length || this.width < 2 || this.height < 2) return;
    /* Fit every district — the shelf keeps them close enough that no
       outlier can shrink the skyline. */
    const ds = this.layout.districts;
    const minX=Math.min(...ds.map(d=>d.x-d.width*.02)), maxX=Math.max(...ds.map(d=>d.x+d.width*1.02));
    const minZ=Math.min(...ds.map(d=>d.z-d.depth*.02)), maxZ=Math.max(...ds.map(d=>d.z+d.depth*1.02));
    const target=new Vector3((minX+maxX)/2,8,(minZ+maxZ)/2);
    const direction=new Vector3(.62,.52,.62).normalize();
    const corners: Vector3[]=[];
    for(const d of ds) {
      const geometry=terraceGeometry(pathHash(d.name)%37);
      const positions=geometry.getAttribute("position");
      for(let i=0;i<positions.count;i+=3) corners.push(new Vector3(d.x+d.width/2+positions.getX(i)*d.width*1.16,0,d.z+d.depth/2+positions.getZ(i)*d.depth*1.16));
      geometry.dispose();
    }
    for(const p of this.layout.nodes) corners.push(new Vector3(p.x,p.y+p.height+16,p.z));
    // Fit the actual activity dock, not an imaginary corner outside the city.
    const agents=this.options.getState().agents??[];
    for(const a of agents.filter(a=>!a.fileId)) {
      const slot=occupancy(agents,a.id);
      corners.push(this.dockPosition(slot).add(new Vector3(-12,12,12)));
    }
    const right=new Vector3(direction.z,0,-direction.x).normalize();
    const up=new Vector3().crossVectors(direction,right).normalize();
    const midpoint=(axis:Vector3)=>{const values=corners.map(p=>p.dot(axis));return (Math.min(...values)+Math.max(...values))/2;};
    target.copy(right).multiplyScalar(midpoint(right)).addScaledVector(up,midpoint(up)).addScaledVector(direction,midpoint(direction));
    const oldPosition=this.camera.position.clone(), oldQuaternion=this.camera.quaternion.clone();
    let lo=30, hi=Math.hypot(maxX-minX,maxZ-minZ)*5+200;
    for(let i=0;i<24;i++) {
      const distance=(lo+hi)/2;
      this.camera.position.copy(target).addScaledVector(direction,distance);
      this.camera.lookAt(target); this.camera.updateMatrixWorld();
      const fits=corners.every(p=>{const q=p.clone().project(this.camera);return Math.abs(q.x)<=.98 && Math.abs(q.y)<=.96 && q.z<1;});
      if(fits) hi=distance; else lo=distance;
    }
    const distance=hi;
    this.fittedDistance=distance;
    this.overviewDistance = distance;
    // Aerial perspective: distance softens into the paper sky.
    this.scene.fog = new Fog(
      (this.scene.background as Color).getHex(),
      distance * 1.15,
      distance * 2.9,
    );
    this.camera.position.copy(oldPosition);this.camera.quaternion.copy(oldQuaternion);this.camera.updateMatrixWorld();
    this.controls.maxDistance=distance*4;this.controls.minDistance=45;
    if(!this.framed) {
      this.camera.position.copy(target).addScaledVector(direction,distance);
      this.controls.target.copy(target);this.framed=true;this.controls.update();
    } else this.frame(target,distance,TIMING.district);
    this.camera.updateMatrixWorld();
    this.keyLight.position.set(target.x-120,240,target.z+120);
    this.keyLight.target.position.copy(target);
    this.kick();
  }
  pick(px: number, py: number): Node3D | null {
    const hit = this.pickSelection(px, py);
    return hit?.kind === "file"
      ? (this.layout?.byId.get(hit.id) ?? null)
      : null;
  }
  pickSelection(
    px: number,
    py: number,
  ): { kind: "file" | "agent"; id: string } | null {
    this.pointer.set((px / this.width) * 2 - 1, 1 - (py / this.height) * 2);
    this.ray.setFromCamera(this.pointer, this.camera);
    const hits = this.ray.intersectObjects(
      [...this.buildings, ...[...this.vehicles.values()].map((v) => v.group)],
      true,
    );
    for (const hit of hits) {
      let obj = hit.object;
      while (obj.parent && obj.parent !== this.scene) obj = obj.parent;
      const agent = obj.userData.agentId as string | undefined;
      if (agent) return { kind: "agent", id: agent };
      const mesh = this.buildings.indexOf(hit.object as InstancedMesh);
      if (mesh >= 0 && hit.instanceId !== undefined)
        return { kind: "file", id: this.ids[mesh][hit.instanceId] };
    }
    return null;
  }
  kick = () => {
    if (!this.disposed && this.raf === null)
      this.raf = requestAnimationFrame(this.tick);
  };
  private tick = (now: number) => {
    this.raf = null;
    if (this.disposed) return;
    const state = this.options.getState();
    const delta = this.lastFrame ? Math.min(64, now - this.lastFrame) : 16;
    this.animationNow = this.lastFrame ? this.animationNow + delta : now;
    this.lastFrame = now;
    if (delta > 24) this.slowFrames++;
    else this.slowFrames = Math.max(0, this.slowFrames - 1);
    if (this.slowFrames > 90) {
      if (this.ratio > 1) {
        this.ratio = Math.max(1, this.ratio - 0.25);
        this.gl.setPixelRatio(this.ratio);
        this.composer.setPixelRatio(this.ratio);
      } else if (this.ao.enabled) this.ao.enabled=false;
      else if (this.gl.shadowMap.enabled) this.gl.shadowMap.enabled = false;
      else this.labelLimit = 8;
      this.slowFrames = 0;
    }
    let busy = this.theme(now);
    const replay = state.trailToken !== this.replayToken;
    this.replayToken = state.trailToken;
    busy =
      this.paintAgents(
        state.agents ?? [],
        this.animationNow,
        replay || !state.animateWorkers,
      ) || busy;
    if (state.selectedId !== this.selected) {
      this.selected = state.selectedId;
      const p = this.layout?.byId.get(this.selected ?? "") as
        | CityPlot
        | undefined;
      if (p && !state.followAgent) {
        const district =
          state.field.byId.get(p.id)?.kind === "dir"
            ? this.layout?.districts.find((d) => d.name === p.cluster)
            : undefined;
        this.frame(
          district
            ? new Vector3(
                district.x + district.width / 2,
                p.y,
                district.z + district.depth / 2,
              )
            : new Vector3(p.x, p.y + p.height / 2, p.z),
          district ? Math.max(district.width, district.depth) * 2 : Math.max(160, this.fittedDistance * .70),
          district ? TIMING.district : TIMING.building,
        );
      }
    }
    if ((state.followAgent ?? null) !== this.followed) {
      this.followed = state.followAgent ?? null;
      this.cameraMotion.cancel();
      this.targetMotion.cancel();
      const v = this.vehicles.get(this.followed ?? "");
      if (v) {
        this.followOffset
          .copy(this.camera.position)
          .sub(this.controls.target)
          .normalize()
          .multiplyScalar(Math.max(110, this.followOffset.length()));
        this.frame(
          v.group.position,
          Math.max(110, this.followOffset.length()),
          TIMING.follow,
        );
      }
    }
    const pos = this.cameraMotion.sample(this.animationNow),
      target = this.targetMotion.sample(this.animationNow);
    if (pos) this.camera.position.set(pos.x, pos.y, pos.z);
    if (target) this.controls.target.set(target.x, target.y, target.z);
    if (this.followed && !this.input && !this.cameraMotion.busy) {
      const v = this.vehicles.get(this.followed);
      if (v) {
        this.controls.target.copy(v.group.position);
        this.camera.position.copy(v.group.position).add(this.followOffset);
      }
    }
    const moved = this.controls.update();
    busy = busy || moved || this.cameraMotion.busy || this.targetMotion.busy;
    busy = this.rise(this.animationNow) || busy;
    busy = this.paintBuildings(state) || busy;
    busy = this.paintPulses(state) || busy;
    this.paintEdges(state);
    this.aoCamera.copy(this.camera);this.aoCamera.layers.set(0);
    this.composer.render();
    this.labels(state);
    this.options.onZoom?.(
      this.overviewDistance / Math.max(1, this.camera.position.distanceTo(this.controls.target)),
    );
    if (busy) this.kick();
    else this.lastFrame = 0;
  };
  private theme(now: number) {
    const skin = palette();
    if (skin !== this.skin) {
      this.skin = skin;
      this.themeAt = now;
      const style = getComputedStyle(document.documentElement);
      this.themeFrom = [
        (this.scene.background as Color).clone(),
        this.solids.color.clone(),
        this.ground.color.clone(),
      ];
      this.themeTo = [
        new Color(style.getPropertyValue("--city-sky").trim() || skin.paper),
        new Color(
          style.getPropertyValue("--city-building").trim() || "#8b9097",
        ),
        new Color(style.getPropertyValue("--city-terrain").trim() || "#34383d"),
      ];
    }
    const t = Math.min(1, (now - this.themeAt) / 220);
    (this.scene.background as Color).lerpColors(
      this.themeFrom[0],
      this.themeTo[0],
      t,
    );
    this.solids.color.lerpColors(this.themeFrom[1], this.themeTo[1], t);
    this.ground.color.lerpColors(this.themeFrom[2], this.themeTo[2], t);
    this.walkwayMaterial.color.copy(this.ground.color);
    this.siteLinesMaterial.opacity=document.documentElement.dataset.theme==="dark"?.018:.035;
    this.floorMaterial.color.copy(this.scene.background as Color).multiplyScalar(2);
    this.factory.body.color.set(document.documentElement.dataset.theme === "dark" ? "#8795a6" : "#8093aa");
    return t < 1;
  }
  private rise(now: number) {
    if (!this.rising || this.riseAt < 0) return false;
    const elapsed = now - this.riseAt;
    if (elapsed < 0) return true;
    let pending = false;
    for (let kind = 0; kind < this.buildings.length; kind++) {
      const mesh = this.buildings[kind],
        plots = this.risePlots[kind],
        delays = this.riseDelays[kind];
      if (!plots || !delays) continue;
      let dirty = false;
      for (let i = 0; i < plots.length; i++) {
        const delay = delays[i];
        if (delay < 0) continue;
        const t = Math.min(1, Math.max(0, (elapsed - delay) / 640));
        if (t < 1) pending = true;
        else {
          delays[i] = -1;
        }
        // easeOutCubic on height; a slight footprint swell keeps it a grow,
        // not a stretch.
        const s = 1 - Math.pow(1 - t, 3);
        const w = 0.82 + 0.18 * s;
        const p = plots[i];
        this.matrix.position.set(p.x, p.y, p.z);
        this.matrix.scale.set(
          p.width * w,
          Math.max(0.001, p.height * s),
          p.depth * w,
        );
        this.matrix.updateMatrix();
        mesh.setMatrixAt(i, this.matrix.matrix);
        dirty = true;
      }
      if (dirty) mesh.instanceMatrix.needsUpdate = true;
    }
    if (!pending) this.rising = false;
    return true;
  }
  private paintBuildings(state: Frame3DState) {
    const key = [
      state.selectedId,
      state.hoverId,
      [...state.edited.keys()].join(","),
      state.matches ? [...state.matches].join(",") : "",
    ].join("|");
    if (key !== this.colorsKey) {
      this.colorsKey = key;
      this.colorAt = this.animationNow;
      this.buildings.forEach((mesh, k) =>
        this.ids[k].forEach((id, i) => {
          mesh.getColorAt(i, this.colorFrom[k][i]);
          const p=this.layout?.byId.get(id);
          const cluster = p?.cluster ?? "";
          // District identity is a stable hash of the district itself —
          // never a hardcoded repository concept.
          const tint = this.districtTint(cluster);
          /* Facades range across a real city's palette — cool concrete,
             warm stone, pale glass — each taking a modest lean from its
             district so neighborhoods read without saturation slop. */
          const wobble=(pathHash(id)%100)/100;
          const base = wobble < .33 ? "#e8e9eb" : wobble < .66 ? "#efe9dd" : "#e2e6ea";
          const lean = (pathHash(id) >> 6) % 3 === 0 ? .9 : .55;
          const c=this.colorTo[k][i]
            .set(base)
            .lerp(tint,lean);
          if(id === state.selectedId) c.lerp(new Color("#8db6e6"),.45);
          else if(id === state.hoverId) c.multiplyScalar(1.13);
          else if(state.hoverId || (state.matches && !state.matches.has(id))) c.multiplyScalar(.76);
          /* A building the agent has changed keeps its mark under any
             hover — activity is the thing this view exists to show. */
          const edit = state.edited.get(id);
          if (edit) {
            const worked = edit.status === "added" ? "#79b38a"
              : edit.status === "deleted" ? "#c47f7f"
              : "#d99a5b";
            c.lerp(new Color(worked), id === state.selectedId ? 0.3 : 0.55);
          }
        }),
      );
    }
    const t = Math.min(1, (this.animationNow - this.colorAt) / TIMING.hover);
    if (t < 1 || this.colorAt !== -Infinity) {
      const color = new Color();
      this.buildings.forEach((mesh, k) => {
        this.ids[k].forEach((_, i) =>
          mesh.setColorAt(
            i,
            color.lerpColors(this.colorFrom[k][i], this.colorTo[k][i], t),
          ),
        );
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      });
      if (t === 1) this.colorAt = -Infinity;
    }
    return t < 1;
  }
  /* A pulse is a ring that lands at a building's base, swells, and dies —
     the building's way of saying "the agent was just here." */
  private paintPulses(state: Frame3DState) {
    const now = this.animationNow;
    for (const p of state.pulses ?? []) {
      if (this.pulses.has(p.id)) continue;
      const plot = this.layout?.byId.get(p.path);
      if (!plot) continue;
      const mat = new MeshBasicMaterial({
        color: this.pulseTones[p.kind] ?? 0x6fb0d8,
        transparent: true,
        opacity: 0.6,
        depthWrite: false,
        side: DoubleSide,
      });
      const mesh = new Mesh(this.pulseGeometry, mat);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(plot.x, plot.y + 0.12, plot.z);
      mesh.raycast = () => {};
      this.scene.add(mesh);
      this.pulses.set(p.id, { mesh, mat, start: now });
    }
    let active = false;
    for (const [id, pu] of this.pulses) {
      const t = (now - pu.start) / 1800;
      if (t >= 1) {
        this.scene.remove(pu.mesh);
        pu.mat.dispose();
        this.pulses.delete(id);
        continue;
      }
      active = true;
      const s = 2.5 + (1 - Math.pow(1 - t, 2)) * 11;
      pu.mesh.scale.set(s, s, 1);
      pu.mat.opacity = 0.6 * (1 - t) * (1 - t);
    }
    return active;
  }
  private paintEdges(state: Frame3DState) {
    const id = state.hoverId ?? state.selectedId;
    const all = state.showAllRelations === true;
    const key = `${this.layout?.signature}|${id}|${all}`;
    if (key === this.edgeKey) return;
    this.edgeKey = key;
    const vertices: number[] = [];
    const colors: number[] = [];
    const dark = document.documentElement.dataset.theme === "dark";
    const faint = new Color(dark ? "#39434f" : "#c3ccd6");
    const lit = new Color(dark ? "#7fb1f2" : "#287df0");
    // A link is sixteen segments; the budget is segments, not links.
    const budget = all ? 16 * 400 : 16 * 80;
    if (this.layout && (all || id))
      for (const l of this.layout.links) {
        const a = this.layout.byId.get(String(l.source)) as
            | CityPlot
            | undefined,
          b = this.layout.byId.get(String(l.target)) as CityPlot | undefined;
        if (!a || !b) continue;
        const hot = id !== null && (a.id === id || b.id === id);
        if (!all && !hot) continue;
        const tone = all && !hot ? faint : lit;
        const from=new Vector3(a.x,a.y+a.height+1,a.z), to=new Vector3(b.x,b.y+b.height+1,b.z);
        for(let j=0;j<16;j++) {
          for(const t of [j/16,(j+1)/16]) {
            const q=from.clone().lerp(to,t);q.y+=Math.sin(Math.PI*t)*Math.min(16,from.distanceTo(to)*.12);
            vertices.push(q.x,q.y,q.z);
            colors.push(tone.r,tone.g,tone.b);
          }
        }
        if (vertices.length >= 6 * budget) break;
      }
    this.edges.geometry.dispose();
    this.edges.geometry = new BufferGeometry();
    this.edges.geometry.setAttribute(
      "position",
      new Float32BufferAttribute(vertices, 3),
    );
    this.edges.geometry.setAttribute(
      "color",
      new Float32BufferAttribute(colors, 3),
    );
  }
  private paintAgents(
    agents: readonly AgentVisualState[],
    now: number,
    direct: boolean,
  ) {
    let busy = false;
    const present = new Set(agents.map((a) => a.id));
    for (const [id, v] of this.vehicles)
      if (!present.has(id)) {
        this.scene.remove(v.group);
        this.scene.remove(v.accent);
        v.accentMaterial.dispose();
        this.scene.remove(v.trail);
        v.trail.geometry.dispose();
        (v.trail.material as LineBasicMaterial).dispose();
        (v.scan.material as MeshBasicMaterial).dispose();
        this.factory.release(v.group);
        this.vehicles.delete(id);
      }
    for (const a of agents) {
      const p = this.layout?.byId.get(a.fileId ?? "") as CityPlot | undefined;
      const roof=roofFootprint(p?.archetype??0);
      const roofX=p?p.x+roof.x*p.width:0,roofZ=p?p.z+roof.z*p.depth:0;
      const slot = occupancy(agents, a.id);
      const dock=this.dockPosition(slot);
      const to = new Vector3(
        p ? roofX + [0, -14, 14][slot % 3] : dock.x,
        p ? p.y + p.height + (a.activity === "editing" ? 32 : 42) : dock.y,
        p ? roofZ + Math.floor(slot / 3) * 13 : dock.z,
      );
      let v = this.vehicles.get(a.id);
      if (!v) {
        const group = this.factory.create(a.color);
        group.rotation.order = "YXZ";
        group.scale.setScalar(1.9);
        group.userData.agentId = a.id;
        const scan = new Mesh(this.scanGeometry, this.scanMaterial.clone());
        scan.layers.set(1);
        (scan.material as MeshBasicMaterial).color.set(a.color);
        scan.position.y = -5;
        scan.raycast = () => {};
        group.add(scan);
        group.position.copy(to);
        const accentMaterial = new MeshBasicMaterial({
          color: a.color,
          transparent: true,
          opacity: 0.3,
          depthWrite: false,
        });
        const accent = new Mesh(this.box, accentMaterial);
        accent.layers.set(1);
        accent.raycast = () => {};
        this.scene.add(accent);
        const trailGeometry = new BufferGeometry();
        trailGeometry.setAttribute(
          "position",
          new Float32BufferAttribute(new Float32Array(39), 3),
        );
        const trail = new Line(
          trailGeometry,
          new LineBasicMaterial({
            color: a.color,
            transparent: true,
            opacity: 0.25,
            depthTest: true,
          }),
        );
        trail.frustumCulled = false;
        this.scene.add(trail);
        v = {
          group,
          scan,
          from: to.clone(),
          to: to.clone(),
          start: now,
          duration: 0,
          event: a.sourceEvent,
          accent,
          accentMaterial,
          activity: a.activity,
          enteredAt: now,
          trail,
        };
        this.vehicles.set(a.id, v);
        this.scene.add(group);
      }
      if (v.event !== a.sourceEvent || v.activity !== a.activity) {
        v.event = a.sourceEvent;
        v.activity = a.activity;
        v.enteredAt = now;
      }
      if (!v.to.equals(to)) {
        v.from.copy(v.group.position);
        v.to.copy(to);
        v.start = now;
        const distance = v.from.distanceTo(to);
        v.duration =
          this.reduced || direct
            ? 0
            : distance < 100
              ? TIMING.local
              : distance < 400
                ? TIMING.neighborhood
                : TIMING.repository;
      }
      if (this.reduced || direct) v.duration = 0;
      const t = v.duration ? Math.min(1, (now - v.start) / v.duration) : 1;
      const point = travel(v.from, v.to, t);
      v.trail.visible = t < 1 && !this.reduced;
      if (v.trail.visible) {
        const positions = v.trail.geometry.getAttribute("position");
        for (let i = 0; i < 13; i++) {
          const tail = travel(v.from, v.to, Math.max(0, t - 0.12 + i * 0.01));
          positions.setXYZ(i, tail.x, tail.y, tail.z);
        }
        positions.needsUpdate = true;
      }
      v.group.position.set(point.x, point.y, point.z);
      const pixelsPerUnit=this.height/(2*Math.tan(this.camera.fov*Math.PI/360)*Math.max(1,this.camera.position.distanceTo(v.group.position)));
      const vehicleScale=Math.max(.85,Math.min(3,40/(18*pixelsPerUnit)));
      v.group.scale.setScalar(vehicleScale);
      if (t < 1) {
        const next = travel(v.from, v.to, Math.min(1, t + 0.005));
        const heading=Math.atan2(-(next.x-point.x),-(next.z-point.z));
        const difference=Math.atan2(Math.sin(heading-v.group.rotation.y),Math.cos(heading-v.group.rotation.y));
        v.group.rotation.y+=difference*.18;
        v.group.rotation.x = Math.atan2(
          next.y - point.y,
          Math.hypot(next.x - point.x, next.z - point.z),
        );
        busy = true;
      } else v.group.rotation.x = 0;
      v.group.children.forEach((child) => {
        if (child.userData.rotorBlade)
          child.rotation.y = this.reduced || !a.running ? 0 : now * 0.015;
      });
      if (
        a.running &&
        a.activity !== "idle" &&
        a.activity !== "outside" &&
        !this.reduced &&
        t === 1
      ) {
        v.group.position.y += Math.sin((now / 3200) * Math.PI * 2) * 0.25;
        busy = true;
      }
      v.scan.visible = ["reading", "editing"].includes(a.activity) && !!p && t === 1;
      if(p) {
        const distance=Math.max(.1,v.group.position.y-p.y-p.height);
        v.scan.position.y=-distance/(2*vehicleScale);
        v.scan.scale.set(p.width*roof.width*.65/vehicleScale,distance/vehicleScale,p.depth*roof.depth*.65/vehicleScale);
      }
      if (p) {
        v.accent.position.set(roofX, p.y + p.height - .12, roofZ);
        v.accent.scale.set(p.width*roof.width*.92, 0.12, p.depth*roof.depth*.92);
      }
      v.accent.visible =
        !!p &&
        t === 1 &&
        ["reading", "editing", "verifying"].includes(a.activity);
      const pulse =
        a.activity === "editing" && !this.reduced
          ? Math.max(0, 1 - (now - v.enteredAt) / 400)
          : 0;
      v.accentMaterial.opacity =
        (a.activity === "reading" ? 0.08 : 0.22) + pulse * 0.25;
      busy = busy || pulse > 0;
    }
    return busy;
  }
  private labels(state: Frame3DState) {
    const ctx = this.options.overlay.getContext("2d");
    if (!ctx || !this.layout) return;
    ctx.clearRect(0, 0, this.width, this.height);
    const boxes: {x:number;y:number;w:number;h:number}[]=[];
    const label=(title:string, detail:string, x:number,y:number,z:number,color?:string) => {
      if(boxes.length>=this.labelLimit)return;
      title=title.length>26?title.slice(0,25)+"…":title;
      this.vector.set(x,y,z).project(this.camera);
      if(this.vector.z < -1 || this.vector.z > 1)return;
      ctx.font="600 12px system-ui";
      const w=Math.max(ctx.measureText(title).width,Math.min(180,detail.length*5.5))+30,h=detail?48:30;
      const sx=((this.vector.x+1)*this.width)/2, sy=((1-this.vector.y)*this.height)/2;
      for(const [dx,dy] of [[14,-36],[-w-14,-36],[14,16],[-w-14,16]]) {
        const bx=sx+dx,by=Math.max(this.height>400?(this.width<600?185:145):10,sy+dy);
        if(bx<10||by<10||bx+w>this.width-10||by+h>this.height-10)continue;
        if(boxes.some(b=>bx<b.x+b.w+8&&bx+w+8>b.x&&by<b.y+b.h+8&&by+h+8>b.y))continue;
        const dark=document.documentElement.dataset.theme==="dark";
        ctx.fillStyle=dark?"rgba(20,25,32,.91)":"rgba(255,255,255,.91)";
        ctx.strokeStyle=dark?"rgba(180,200,220,.12)":"rgba(70,85,105,.10)";
        ctx.beginPath();ctx.roundRect(bx,by,w,h,7);ctx.fill();ctx.stroke();
        ctx.fillStyle=color??(dark?"#aebbd0":"#7894b2");ctx.beginPath();ctx.arc(bx+12,by+15,3,0,Math.PI*2);ctx.fill();
        ctx.fillStyle=palette().ink;ctx.font="600 12px system-ui";ctx.fillText(title,bx+22,by+19);
        if(detail){ctx.font="10px system-ui";ctx.fillStyle=palette().ink2;ctx.fillText(detail.slice(0,33),bx+22,by+35);}
        boxes.push({x:bx,y:by,w,h});return;
      }
    };
    for(const a of state.agents??[]) {
      const v=this.vehicles.get(a.id);
      if(v && (a.running || state.followAgent===a.id)) label(a.label,a.location==="unknown"?"Location unknown":a.activity,v.group.position.x,v.group.position.y+5,v.group.position.z,a.color);
    }
    if(state.selectedId) {
      const p=this.layout.byId.get(state.selectedId) as CityPlot|undefined;
      if(p)label(state.field.byId.get(p.id)?.label??p.id,"Selected file",p.x,p.y+p.height+3,p.z,"#287df0");
    }
    const named=new Set<string>();
    for(const d of this.layout.districts) {
      if(named.has(d.name))continue;named.add(d.name);
      const count=this.layout.nodes.filter(p=>p.cluster===d.name).length;
      if(!count)continue;
      label(d.name||"Repository",`${count.toLocaleString()} files`,d.x+d.width*.2,3.5,d.z+d.depth*.15);
    }
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.controls.removeEventListener("start", this.onStart);
    this.controls.removeEventListener("end", this.onEnd);
    this.controls.removeEventListener("change", this.kick);
    this.controls.dispose();
    this.buildings.forEach((m) => m.dispose());
    this.terrain.traverse((o) => {
      if (o instanceof InstancedMesh) o.dispose();
    });
    this.terraces.forEach(g => g.dispose());
    this.geometry.forEach((g) => g.dispose());
    this.box.dispose();
    this.scanGeometry.dispose();
    this.scanMaterial.dispose();
    this.solids.dispose();
    this.ground.dispose();
    this.contact.dispose();
    this.edges.geometry.dispose();
    (this.edges.material as LineBasicMaterial).dispose();
    this.vehicles.forEach((v) => {
      (v.scan.material as MeshBasicMaterial).dispose();
      this.factory.release(v.group);
      v.accentMaterial.dispose();
      v.trail.geometry.dispose();
      (v.trail.material as LineBasicMaterial).dispose();
    });
    this.keyLight.shadow.map?.dispose();
    this.factory.dispose();
    this.floorGeometry.dispose();this.floorMaterial.dispose();
    this.siteLinesMaterial.dispose();this.siteMarkGeometry.dispose();this.siteMarkMaterial.dispose();this.siteMarkTexture.dispose();
    this.walkwayMaterial.dispose();
    this.ao.dispose();this.renderPass.dispose();this.outputPass.dispose();this.composer.dispose();
    this.scene.clear();
    this.gl.dispose();
    this.gl.forceContextLoss();
  }
}

function districtCenter(d:District) { return new Vector3(d.x+d.width/2,0,d.z+d.depth/2); }
