/* ------------------------------------------------------------------ *
 * The field, drawn in depth.
 *
 * Everything the flat painter draws, drawn again against the same data:
 * relations first, then what an agent is doing to a file, then the files
 * themselves, then who is standing on them, then whatever is travelling.
 * The order is the flat painter's order and the colours are the flat
 * painter's colours — imported from it, not copied — because this is one
 * map with two projections and the two must not be able to disagree.
 *
 * Five draw calls, whatever the size of the repository: one run of lines
 * for every relation, one instanced quad for every file, two for the
 * rings around them, one for whatever is in flight. Nothing is allocated
 * inside a frame. React never re-renders because of one.
 *
 * The layout is a force simulation on three axes and the camera is an
 * orbit; both are described where they live. What is decided *here* is
 * how depth is allowed to show: near particles are larger because of the
 * perspective divide, far ones fade toward the paper colour, and that is
 * all. No lights, no bloom, no glow past what the flat view already has.
 * A relation you cannot follow is the only real failure mode of this
 * view, and every one of those effects makes relations harder to follow.
 * ------------------------------------------------------------------ */

import {
  Color,
  ColorManagement,
  LineBasicMaterial,
  PerspectiveCamera,
  Scene,
  ShaderMaterial,
  Vector3,
  WebGLRenderer,
} from "three";

/* Every colour in this view is a value read straight off the stylesheet —
   the same `--paper`, `--ink` and agent hues the flat canvas paints with.
   three treats colours as linear working space and converts on output,
   which lifted `#0f1113` to a mid grey and washed the particles out. We
   are not lighting surfaces here, we are reproducing an interface, so the
   conversion is turned off and the tokens land exactly as authored. */
ColorManagement.enabled = false;
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Simulation3D } from "d3-force-3d";

import type { DiffFile } from "../api";
import { occupantsOf, type Occupant } from "../agentField";
import { hueRgb, type ParticleField } from "../graph";
import {
  FILAMENT_ALPHA,
  DIMMED,
  FOCUS_DIM,
  GLOW_ALPHA,
  GLOW_MIN_HEAT,
  GLOW_SPREAD,
  HALO_MS,
  RING_GAP,
  SLOT_GAP,
  WORKER_HALO_MS,
  type WorkerLayer,
} from "../graphDraw";
import {
  bounds3d,
  controlOf,
  depthOrder,
  drawnSegments,
  fitDistance,
  FIT_DIRECTION,
  hitTest3d,
  pointOn3d,
  PROJECT_STRIDE,
  project3d,
  type Node3D,
  type Scene3D,
  type Vec3,
} from "../graph3d";
import { createSim3d, settle3d } from "../graphSim3d";
import { hexRgb, listRgb, palette, type Palette, type Rgb } from "../palette";
import {
  MAX_IN_FLIGHT,
  PRIMARY_RGB,
  TAIL,
  type LiveSignal,
  type SignalDirector,
} from "../signals";
import { flareAt, FLARE_REACH, type ArrivalWatch } from "../signalsView";
import { attentionAlpha, type Attention } from "../trail";
import { DiscSet, FULL_TURN, LineSet, RingSet } from "./buffers";
import { paintLabels3d } from "./labels3d";
import { applyFieldUniforms, discMaterial, ringMaterial } from "./materials";
import { Disposer } from "../dispose";

/* ---- the same numbers the flat view uses, in world units ---- */

const FOV = 45;
/** How long the hover dim takes to come on. The flat view's `DIM_MS`. */
const DIM_MS = 120;
/** A whole turn takes about three and a half minutes. */
const AUTO_ORBIT_SPEED = 0.3;
/** Devices report 3 and 4; past 2 it is fill rate for nothing. */
const MAX_DPR = 2;
/** The tail of a travelling signal, in segments. */
const TAIL_STEPS = 6;
/** A still signal lights the whole filament, so it needs the whole arc. */
const STILL_STEPS = 12;
/** How many arrivals may be alight at once. */
const FLARE_SLOTS = 24;

/**
 * Relations are a little fainter here than on the flat canvas.
 *
 * Not a different vocabulary — the same table, scaled once. The flat view
 * shows one plane of the graph at a time; this one shows all of it at
 * once, with everything behind everything else, so the same alpha adds up
 * to a denser picture. The ratios between the kinds are what carry the
 * distinction, and those are untouched.
 *
 * Raised from 0.86 after looking at it: depth costs a relation twice, once
 * to the fog and once to being seen end-on, and at the flat view's alpha
 * the connections simply were not there — a field of unconnected dots,
 * which is the one thing this view exists not to be. The kinds keep their
 * ratio, so a call still reads louder than an import.
 */
const EDGE_SCALE = 1.8;

export interface Frame3DState {
  field: ParticleField;
  neighbours: ReadonlyMap<string, ReadonlySet<string>>;
  attention: ReadonlyMap<string, Attention>;
  currentStep: number;
  edited: ReadonlyMap<string, DiffFile>;
  positionId: string | null;
  running: boolean;
  selectedId: string | null;
  hoverId: string | null;
  matches: ReadonlySet<string> | null;
  labels: boolean;
  workers: readonly WorkerLayer[];
  presence: ReadonlyMap<string, readonly string[]>;
  focusAgent: string | null;
  reduced: boolean;
  director: SignalDirector;
  arrivals: ArrivalWatch;
}

export interface Renderer3DOptions {
  canvas: HTMLCanvasElement;
  /** The flat canvas the names are drawn on, over the field. */
  overlay: HTMLCanvasElement;
  getState: () => Frame3DState;
  /** How far in the camera is, as a factor of the framing distance. */
  onZoom?: (k: number) => void;
}

interface Flare {
  id: string;
  color: Rgb;
  at: number;
  live: boolean;
}

const NO_RGB: Rgb = { r: 0, g: 0, b: 0 };

export class Renderer3D {
  private readonly gl: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly camera: PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly overlay: CanvasRenderingContext2D | null;
  private readonly getState: () => Frame3DState;
  private readonly onZoom: ((k: number) => void) | undefined;
  private readonly bin = new Disposer();

  private readonly particleMaterial: ShaderMaterial;
  private readonly sparkMaterial: ShaderMaterial;
  private readonly ringMat: ShaderMaterial;
  private readonly lineMaterial: LineBasicMaterial;

  private layout: Scene3D | null = null;
  private sim: Simulation3D<Node3D, unknown> | null = null;
  private discs: DiscSet | null = null;
  private sparks: DiscSet | null = null;
  private under: RingSet | null = null;
  private over: RingSet | null = null;
  private edges: LineSet | null = null;
  private tails: LineSet | null = null;

  private screen = new Float32Array(0);
  private order = new Uint32Array(0);
  private readonly slots: Occupant<WorkerLayer>[] = [];
  private readonly layerIndex = new Map<string, WorkerLayer>();
  private indexedFor: readonly WorkerLayer[] | null = null;

  private readonly flares: Flare[] = [];
  private flareAt = 0;

  private raf: number | null = null;
  private lastFrame = 0;
  private dim = 0;
  private width = 1;
  private height = 1;
  private halfHeight = 1;
  private framed = false;
  private frameDistance = 1;
  private layoutDirty = true;
  private autoOrbit = true;
  private reduced = false;
  private disposed = false;
  private controlsMoved = true;

  /* Reused every frame. The hot loop allocates nothing. */
  private readonly a: Vec3 = { x: 0, y: 0, z: 0 };
  private readonly b: Vec3 = { x: 0, y: 0, z: 0 };
  private readonly control: Vec3 = { x: 0, y: 0, z: 0 };
  private readonly point: Vec3 = { x: 0, y: 0, z: 0 };
  private readonly last: Vec3 = { x: 0, y: 0, z: 0 };
  private readonly viewProjection = new Float32Array(16);
  private readonly focus = new Vector3();

  private skin: Palette | null = null;
  private paper: Rgb = NO_RGB;
  private ink: Rgb = NO_RGB;
  private accent: Rgb = NO_RGB;
  private change: Rgb = NO_RGB;
  private readonly clear = new Color();

  /* What the edge colours were last computed for. They only change when
     one of these does, so a settled field re-uploads positions and
     nothing else. */
  private edgeFor = {
    hover: "\u0000",
    dim: -1,
    matches: undefined as ReadonlySet<string> | null | undefined,
    skin: null as Palette | null,
  };

  constructor(options: Renderer3DOptions) {
    this.getState = options.getState;
    this.onZoom = options.onZoom;

    this.gl = new WebGLRenderer({
      canvas: options.canvas,
      /* The discs and rings antialias themselves analytically, so
         multisampling would only smooth the hairlines — and it would do
         it at four times the fill rate, which is exactly the budget a
         software rasteriser does not have. */
      antialias: false,
      alpha: false,
      stencil: false,
      depth: false,
      powerPreference: "default",
    });
    this.bin.add(this.gl);
    this.bin.add(() => this.gl.forceContextLoss());

    this.camera = new PerspectiveCamera(FOV, 1, 1, 4000);
    this.controls = new OrbitControls(this.camera, options.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.rotateSpeed = 0.7;
    this.controls.zoomSpeed = 0.8;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = AUTO_ORBIT_SPEED;
    this.bin.add(this.controls);

    /* The idle orbit exists so a field that nobody is touching still
       reads as a volume. The moment somebody touches it, it is theirs:
       it stops, and it does not come back and take the camera off them
       later. */
    const onStart = () => this.stopAutoOrbit();
    const onChange = () => {
      this.controlsMoved = true;
      this.kick();
    };
    this.controls.addEventListener("start", onStart);
    this.controls.addEventListener("change", onChange);
    this.bin.add(() => {
      this.controls.removeEventListener("start", onStart);
      this.controls.removeEventListener("change", onChange);
    });

    this.particleMaterial = this.bin.add(discMaterial(0.42));
    this.sparkMaterial = this.bin.add(discMaterial(0));
    this.ringMat = this.bin.add(ringMaterial());
    this.lineMaterial = this.bin.add(
      new LineBasicMaterial({
        vertexColors: true,
        transparent: false,
        depthTest: false,
        depthWrite: false,
      }),
    );

    this.overlay = options.overlay.getContext("2d");
    this.refreshTheme();
    for (let i = 0; i < FLARE_SLOTS; i += 1) {
      this.flares.push({ id: "", color: NO_RGB, at: 0, live: false });
    }
    this.bin.add(() => this.scene.clear());
  }

  /* ---------------- lifecycle ---------------- */

  resize(width: number, height: number, dpr: number): void {
    if (this.disposed || width < 2 || height < 2) return;
    this.width = width;
    this.height = height;
    const ratio = Math.min(MAX_DPR, Math.max(1, dpr));
    this.halfHeight = (height * ratio) / 2;
    this.gl.setPixelRatio(ratio);
    this.gl.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    if (!this.framed) this.fit();
    this.kick();
  }

  setReduced(reduced: boolean): void {
    if (this.reduced === reduced) return;
    this.reduced = reduced;
    /* Reduced motion is about motion nobody asked for. Dragging still
       orbits; the camera just never moves on its own again, and it never
       glides on after a release. */
    this.controls.enableDamping = !reduced;
    if (reduced) this.stopAutoOrbit();
    this.clearFlares();
    this.kick();
  }

  /**
   * Take a layout. Everything sized by the field is rebuilt here and
   * nowhere else — a frame never allocates, so a frame never grows a
   * buffer either.
   */
  setLayout(layout: Scene3D, carriedOver: boolean): void {
    if (this.disposed) return;
    this.releaseSets();
    this.layout = layout;
    this.sim = null;

    const n = layout.nodes.length;
    if (n === 0) {
      this.screen = new Float32Array(0);
      this.order = new Uint32Array(0);
      this.kick();
      return;
    }

    this.screen = new Float32Array(n * PROJECT_STRIDE);
    this.order = new Uint32Array(n);

    const edgeSegments = Math.max(1, layout.edgeVertices / 2);
    this.edges = new LineSet(edgeSegments, this.lineMaterial, 0);
    this.under = new RingSet(n * 2 + FLARE_SLOTS + 32, this.ringMat, 1);
    this.discs = new DiscSet(n, this.particleMaterial, 2);
    this.over = new RingSet(n * 3 + 64, this.ringMat, 3);
    this.tails = new LineSet(MAX_IN_FLIGHT * STILL_STEPS, this.lineMaterial, 4);
    this.sparks = new DiscSet(MAX_IN_FLIGHT, this.sparkMaterial, 5);

    for (const set of [
      this.edges,
      this.under,
      this.discs,
      this.over,
      this.tails,
      this.sparks,
    ]) {
      this.scene.add(set.mesh);
    }

    this.sim = createSim3d(layout) as unknown as Simulation3D<Node3D, unknown>;
    settle3d(
      this.sim as unknown as Parameters<typeof settle3d>[0],
      carriedOver,
    );
    this.layoutDirty = true;
    this.edgeFor.skin = null;
    if (!this.framed) this.fit();
    this.kick();
  }

  /** Frame the whole field. Explicit: a re-fit is something asked for. */
  fit(): void {
    const layout = this.layout;
    if (!layout) return;
    const ball = bounds3d(layout.nodes);
    if (!ball) return;

    const distance = fitDistance(ball.r, FOV, this.camera.aspect);
    const len =
      Math.hypot(FIT_DIRECTION.x, FIT_DIRECTION.y, FIT_DIRECTION.z) || 1;
    this.camera.position.set(
      ball.x + (FIT_DIRECTION.x / len) * distance,
      ball.y + (FIT_DIRECTION.y / len) * distance,
      ball.z + (FIT_DIRECTION.z / len) * distance,
    );
    this.controls.target.set(ball.x, ball.y, ball.z);
    this.camera.near = Math.max(0.5, (distance - ball.r) * 0.2);
    this.camera.far = distance + ball.r * 6;
    this.camera.updateProjectionMatrix();
    this.controls.minDistance = Math.max(1, ball.r * 0.2);
    this.controls.maxDistance = distance * 3.5;
    this.controls.update();

    this.frameDistance = distance;
    this.framed = true;
    this.controlsMoved = true;
    this.kick();
  }

  stopAutoOrbit(): void {
    if (!this.autoOrbit) return;
    this.autoOrbit = false;
    this.controls.autoRotate = false;
  }

  /** The particle under a canvas point, from the last frame's projection. */
  pick(px: number, py: number): Node3D | null {
    const layout = this.layout;
    if (!layout) return null;
    const at = hitTest3d(this.screen, layout.nodes.length, px, py);
    return at < 0 ? null : layout.nodes[at];
  }

  kick(): void {
    if (this.disposed || this.raf !== null) return;
    this.raf = requestAnimationFrame((now) => this.tick(now));
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.raf = null;
    this.sim?.stop();
    this.sim = null;
    this.releaseSets();
    this.bin.dispose();
  }

  private releaseSets(): void {
    for (const set of [
      this.edges,
      this.under,
      this.discs,
      this.over,
      this.tails,
      this.sparks,
    ]) {
      if (!set) continue;
      this.scene.remove(set.mesh);
      set.dispose();
    }
    this.edges = null;
    this.under = null;
    this.discs = null;
    this.over = null;
    this.tails = null;
    this.sparks = null;
  }

  /* ---------------- the frame ---------------- */

  private tick(now: number): void {
    this.raf = null;
    if (this.disposed) return;
    const state = this.getState();
    const layout = this.layout;

    const dt = this.lastFrame === 0 ? 16 : Math.min(64, now - this.lastFrame);
    this.lastFrame = now;

    this.refreshTheme();
    this.gl.setClearColor(this.clear, 1);

    if (!layout || layout.nodes.length === 0) {
      this.gl.clear();
      this.overlay?.clearRect(0, 0, this.width, this.height);
      this.lastFrame = 0;
      return;
    }

    /* ---- the layout ---- */
    const sim = this.sim;
    const settling = sim !== null && sim.alpha() > sim.alphaMin();
    if (settling) {
      sim.tick();
      this.layoutDirty = true;
    }

    /* ---- the camera ---- */
    const orbiting = this.controls.autoRotate;
    this.controlsMoved = false;
    this.controls.update();
    const cameraMoved = this.controlsMoved || orbiting;
    this.focus.copy(this.camera.position).sub(this.controls.target);
    const distance = Math.max(1, this.focus.length());
    if (this.onZoom) this.onZoom(this.frameDistance / distance);

    const ball = bounds3d(layout.nodes);
    const spread = ball ? ball.r : distance;
    const fogNear = Math.max(1, distance - spread * 0.85);
    const fogFar = distance + spread * 1.3;
    for (const material of [
      this.particleMaterial,
      this.sparkMaterial,
      this.ringMat,
    ]) {
      applyFieldUniforms(
        material,
        this.ink,
        this.paper,
        fogNear,
        fogFar,
        this.halfHeight,
      );
    }

    /* ---- the hover dim, on the flat view's clock ---- */
    const target = state.hoverId ? 1 : 0;
    if (this.dim !== target) {
      const step = dt / DIM_MS;
      this.dim =
        target > this.dim
          ? Math.min(1, this.dim + step)
          : Math.max(0, this.dim - step);
    }
    const tweening = this.dim !== target;

    /* ---- projection, which the labels and the hit test both read ---- */
    this.camera.updateMatrixWorld();
    const vp = this.viewProjection;
    multiply(
      this.camera.projectionMatrix.elements,
      this.camera.matrixWorldInverse.elements,
      vp,
    );
    project3d(vp, layout.nodes, this.width, this.height, this.screen);
    depthOrder(this.screen, layout.nodes.length, this.order);

    /* ---- what is in the air, and what has just landed ---- */
    const travelling = state.director.update(now);
    const landed = state.arrivals.observe(travelling, now, state.reduced);
    for (let i = 0; i < landed.length; i += 1) this.light(landed[i]);
    const flaring = this.paintFlares(state, now);

    this.paintEdges(state);
    this.paintParticles(state, now);
    this.paintRings(state, now);
    this.paintSignals(state, travelling);

    this.gl.render(this.scene, this.camera);

    if (this.overlay) {
      paintLabels3d({
        ctx: this.overlay,
        width: this.width,
        height: this.height,
        field: state.field,
        nodes: layout.nodes,
        screen: this.screen,
        order: this.order,
        count: layout.nodes.length,
        labels: state.labels,
        hoverId: state.hoverId,
        selectedId: state.selectedId,
        positionId: state.positionId,
        fogNear,
        fogFar,
        ink: this.skin?.ink ?? "#e6e6e6",
        ink2: this.skin?.ink2 ?? "#8b8f97",
      });
    }

    const halo =
      !state.reduced &&
      ((state.running && state.positionId !== null) ||
        state.workers.some(
          (layer) => layer.running && layer.positionId !== null,
        ));

    if (
      settling ||
      cameraMoved ||
      tweening ||
      flaring ||
      halo ||
      state.director.busy
    ) {
      this.kick();
    } else {
      this.lastFrame = 0;
    }
  }

  /* ---------------- theme ---------------- */

  private refreshTheme(): void {
    const skin = palette();
    if (skin === this.skin) return;
    this.skin = skin;
    this.paper = hexRgb(skin.paper);
    this.ink = hexRgb(skin.ink);
    this.accent = listRgb(skin.accent);
    this.change = listRgb(skin.change);
    this.clear.setRGB(this.paper.r, this.paper.g, this.paper.b);
    this.edgeFor.skin = null;
  }

  /* ---------------- relations ---------------- */

  private paintEdges(state: Frame3DState): void {
    const edges = this.edges;
    const layout = this.layout;
    if (!edges || !layout) return;

    const colorsStale =
      this.edgeFor.skin !== this.skin ||
      this.edgeFor.hover !== (state.hoverId ?? "\u0000") ||
      this.edgeFor.matches !== state.matches ||
      Math.abs(this.edgeFor.dim - this.dim) > 0.02;

    if (!this.layoutDirty && !colorsStale) return;

    edges.begin();
    const paper = this.paper;

    for (const link of layout.links) {
      const a = endOf(layout, link.source);
      const b = endOf(layout, link.target);
      if (!a || !b) continue;

      const base = (FILAMENT_ALPHA[link.kind] ?? 0.14) * EDGE_SCALE;
      const touched =
        state.hoverId !== null &&
        (a.id === state.hoverId || b.id === state.hoverId);
      let alpha = touched
        ? lerp(base, 0.62, this.dim)
        : base * lerp(1, DIMMED, this.dim);
      if (
        state.matches &&
        !state.matches.has(a.id) &&
        !state.matches.has(b.id)
      ) {
        alpha *= DIMMED;
      }
      if (alpha < 0.01) {
        /* Still consumes its segments: the buffer layout has to stay put
           between frames, and a relation that is merely too faint to see
           is not a relation that has gone away. */
        skipSegments(edges, link.samples, link.dashed);
        continue;
      }

      const tint = link.kind === "cotouch" ? this.change : this.ink;
      const cr = paper.r + (tint.r - paper.r) * alpha;
      const cg = paper.g + (tint.g - paper.g) * alpha;
      const cb = paper.b + (tint.b - paper.b) * alpha;

      this.a.x = a.x;
      this.a.y = a.y;
      this.a.z = a.z;
      this.b.x = b.x;
      this.b.y = b.y;
      this.b.z = b.z;
      controlOf(layout, a, b, link.across, this.control);

      const steps = link.samples - 1;
      this.last.x = a.x;
      this.last.y = a.y;
      this.last.z = a.z;
      for (let i = 1; i <= steps; i += 1) {
        pointOn3d(this.a, this.control, this.b, i / steps, this.point);
        /* Dashed relations draw the even segments only — the flat view's
           `[3, 3]` dash, in the only currency a line renderer has. */
        if (!link.dashed || (i - 1) % 2 === 0) {
          edges.add(
            this.last.x,
            this.last.y,
            this.last.z,
            this.point.x,
            this.point.y,
            this.point.z,
            cr,
            cg,
            cb,
            cr,
            cg,
            cb,
          );
        }
        this.last.x = this.point.x;
        this.last.y = this.point.y;
        this.last.z = this.point.z;
      }
    }

    edges.end();
    this.layoutDirty = false;
    this.edgeFor.skin = this.skin;
    this.edgeFor.hover = state.hoverId ?? "\u0000";
    this.edgeFor.matches = state.matches;
    this.edgeFor.dim = this.dim;
  }

  /* ---------------- particles ---------------- */

  private paintParticles(state: Frame3DState, _now: number): void {
    const discs = this.discs;
    const layout = this.layout;
    if (!discs || !layout) return;

    const near = state.hoverId
      ? state.neighbours.get(state.hoverId)
      : undefined;

    discs.begin();
    /* Furthest first, so a file in front covers one behind it. */
    for (let k = 0; k < layout.nodes.length; k += 1) {
      const node = layout.nodes[this.order[k]];
      const at = this.order[k] * PROJECT_STRIDE;
      if (this.screen[at + 2] <= 0) continue;

      const edit = state.edited.get(node.id);
      const isPosition = node.id === state.positionId;

      let r = node.r;
      if (edit) r *= 1.2;
      if (isPosition) r += this.perPixel(this.order[k]) * 2;

      const related =
        state.hoverId === null ||
        node.id === state.hoverId ||
        near?.has(node.id) === true;
      let alpha = related ? 1 : lerp(1, DIMMED, this.dim);
      if (state.matches && !state.matches.has(node.id)) alpha *= DIMMED;
      if (alpha < 0.02) continue;

      let color: Rgb;
      if (edit) color = this.change;
      else if (isPosition) color = this.accent;
      else color = hueRgb(node.hue);

      let cr = color.r;
      let cg = color.g;
      let cb = color.b;

      /* The read flare: the same decay the flat view paints over the fill
         rather than beside it, so a file the agent has just looked at is
         the same colour in both views. */
      const seen = state.attention.get(node.id);
      const heat = seen ? attentionAlpha(seen.last, state.currentStep) : 0;
      if (heat > 0 && !edit && !isPosition) {
        const mix = heat * 0.75;
        cr += (this.accent.r - cr) * mix;
        cg += (this.accent.g - cg) * mix;
        cb += (this.accent.b - cb) * mix;
      }

      discs.add(node.x, node.y, node.z, r, cr, cg, cb, alpha);
    }
    discs.end();
  }

  /* ---------------- rings, halos and wedges ---------------- */

  private paintRings(state: Frame3DState, now: number): void {
    const under = this.under;
    const over = this.over;
    const layout = this.layout;
    if (!under || !over || !layout) return;

    under.begin();
    over.begin();

    /* The arrival flares were queued before the rings, so they are drawn
       under the particles the way the agent glow is. */
    this.emitFlares(under, state, now);

    const index = this.layersById(state.workers);
    const halo =
      state.running && !state.reduced ? (now % HALO_MS) / HALO_MS : null;
    const workerPulse = state.reduced
      ? 0
      : (now % WORKER_HALO_MS) / WORKER_HALO_MS;

    for (let k = 0; k < layout.nodes.length; k += 1) {
      const i = this.order[k];
      const node = layout.nodes[i];
      const at = i * PROJECT_STRIDE;
      if (this.screen[at + 2] <= 0) continue;
      const px = this.perPixel(i);

      const edit = state.edited.get(node.id);
      const isPosition = node.id === state.positionId;
      const seen = state.attention.get(node.id);
      const heat = seen ? attentionAlpha(seen.last, state.currentStep) : 0;
      let r = node.r;
      if (edit) r *= 1.2;
      if (isPosition) r += px * 2;

      if (isPosition) {
        over.add(
          node.x,
          node.y,
          node.z,
          r + px * 2.5,
          px * 2,
          this.accent.r,
          this.accent.g,
          this.accent.b,
          0.95,
        );
        if (halo !== null) {
          over.add(
            node.x,
            node.y,
            node.z,
            r * (1 + 1.2 * halo),
            px * 1.25,
            this.accent.r,
            this.accent.g,
            this.accent.b,
            0.5 * (1 - halo),
          );
        }
      } else if (edit) {
        over.add(
          node.x,
          node.y,
          node.z,
          r + px * 2.5,
          px * 1.25,
          this.change.r,
          this.change.g,
          this.change.b,
          0.75,
        );
      } else if (heat > 0) {
        over.add(
          node.x,
          node.y,
          node.z,
          r + px * 3,
          px * 1.25,
          this.accent.r,
          this.accent.g,
          this.accent.b,
          0.35 + heat * 0.5,
        );
      }

      if (node.id === state.selectedId) {
        over.add(
          node.x,
          node.y,
          node.z,
          r + px * 5,
          px,
          this.ink.r,
          this.ink.g,
          this.ink.b,
          0.55,
        );
      }

      const agents = state.presence.get(node.id);
      if (!agents || agents.length === 0) continue;
      const n = occupantsOf(node.id, agents, index, this.slots);
      if (n === 0) continue;

      const wedges = Math.max(1, agents.length);
      const segment = FULL_TURN / wedges;
      const gap = wedges === 1 ? 0 : Math.min(SLOT_GAP, segment * 0.18);
      const ringR = r + px * RING_GAP;

      for (let s = 0; s < n; s += 1) {
        const { layer, here, heat: warmth, slot } = this.slots[s];
        const focus = focusAlpha(state.focusAgent, layer.id);
        const rgb = listRgb(layer.rgb);

        /* Under the particle: someone is in this file *now*. Faint, and
           short — the wedges carry the rest of the trail, and a glow that
           decayed on the same curve would just be a blurrier copy. */
        if (here || warmth >= GLOW_MIN_HEAT) {
          const strength = here ? 1 : warmth;
          under.add(
            node.x,
            node.y,
            node.z,
            r + px * GLOW_SPREAD * 0.6,
            px * GLOW_SPREAD * 1.5,
            rgb.r,
            rgb.g,
            rgb.b,
            (GLOW_ALPHA * strength * focus) / n,
          );
        }

        const alpha = (here ? 0.9 : 0.28 + warmth * 0.45) * focus;
        if (alpha < 0.02) continue;
        /* Slot 0 starts at the top and they run clockwise, so the order
           on screen is the order in the legend — and the same order the
           flat view puts them in. */
        const from = slot * segment + gap / 2;
        const span = segment - gap;
        over.add(
          node.x,
          node.y,
          node.z,
          ringR,
          px * (here ? 1.9 : 1.25),
          rgb.r,
          rgb.g,
          rgb.b,
          alpha,
          from,
          span,
        );

        if (here && layer.running && !state.reduced) {
          over.add(
            node.x,
            node.y,
            node.z,
            ringR + workerPulse * px * 5,
            px * 1.1,
            rgb.r,
            rgb.g,
            rgb.b,
            0.34 * (1 - workerPulse) * focus,
            from,
            span,
          );
        }
      }
    }

    under.end();
    over.end();
  }

  /* ---------------- signals ---------------- */

  private paintSignals(
    state: Frame3DState,
    signals: readonly LiveSignal[],
  ): void {
    const tails = this.tails;
    const sparks = this.sparks;
    const layout = this.layout;
    if (!tails || !sparks || !layout) return;

    tails.begin();
    sparks.begin();
    const paper = this.paper;

    for (let s = 0; s < signals.length; s += 1) {
      const signal = signals[s];
      const from = layout.byId.get(signal.from);
      const to = layout.byId.get(signal.to);
      if (!from || !to) continue;
      const focus = focusAlpha(state.focusAgent, signal.agentId);
      if (focus <= 0.02) continue;

      const rgb = listRgb(signal.rgb || PRIMARY_RGB);
      this.a.x = from.x;
      this.a.y = from.y;
      this.a.z = from.z;
      this.b.x = to.x;
      this.b.y = to.y;
      this.b.z = to.z;
      /* The same arc the relation between these two lobes takes, so a
         signal travels the tract rather than cutting across it — even
         where the two files have no declared relation and there is no
         line under it. */
      controlOf(layout, from, to, from.cluster !== to.cluster, this.control);

      const px =
        (this.perPixelOf(from) + this.perPixelOf(to)) / 2;

      if (signal.still) {
        const alpha = 0.5 * signal.fade * focus;
        this.trace(tails, 0, 1, STILL_STEPS, rgb, paper, alpha, alpha);
        pointOn3d(this.a, this.control, this.b, 1, this.point);
        sparks.add(
          this.point.x,
          this.point.y,
          this.point.z,
          px * 2.4,
          rgb.r,
          rgb.g,
          rgb.b,
          0.85 * signal.fade * focus,
        );
        continue;
      }

      const head = signal.progress;
      const tailStart = Math.max(0, head - TAIL);
      this.trace(tails, tailStart, head, TAIL_STEPS, rgb, paper, 0, 0.85 * focus);
      pointOn3d(this.a, this.control, this.b, head, this.point);
      sparks.add(
        this.point.x,
        this.point.y,
        this.point.z,
        px * 2.4,
        rgb.r,
        rgb.g,
        rgb.b,
        0.95 * focus,
      );
    }

    tails.end();
    sparks.end();
  }

  /** A run along the current arc, fading from `alphaFrom` to `alphaTo`. */
  private trace(
    into: LineSet,
    from: number,
    to: number,
    steps: number,
    rgb: Rgb,
    paper: Rgb,
    alphaFrom: number,
    alphaTo: number,
  ): void {
    pointOn3d(this.a, this.control, this.b, from, this.last);
    let lastAlpha = alphaFrom;
    for (let i = 1; i <= steps; i += 1) {
      const t = from + ((to - from) * i) / steps;
      pointOn3d(this.a, this.control, this.b, t, this.point);
      const alpha = alphaFrom + (alphaTo - alphaFrom) * (i / steps);
      into.add(
        this.last.x,
        this.last.y,
        this.last.z,
        this.point.x,
        this.point.y,
        this.point.z,
        paper.r + (rgb.r - paper.r) * lastAlpha,
        paper.g + (rgb.g - paper.g) * lastAlpha,
        paper.b + (rgb.b - paper.b) * lastAlpha,
        paper.r + (rgb.r - paper.r) * alpha,
        paper.g + (rgb.g - paper.g) * alpha,
        paper.b + (rgb.b - paper.b) * alpha,
      );
      this.last.x = this.point.x;
      this.last.y = this.point.y;
      this.last.z = this.point.z;
      lastAlpha = alpha;
    }
  }

  /* ---------------- arrivals ---------------- */

  private light(arrival: { id: string; rgb: string; at: number }): void {
    const slot = this.flares[this.flareAt];
    this.flareAt = (this.flareAt + 1) % FLARE_SLOTS;
    slot.id = arrival.id;
    slot.color = listRgb(arrival.rgb);
    slot.at = arrival.at;
    slot.live = true;
  }

  private clearFlares(): void {
    for (const flare of this.flares) flare.live = false;
  }

  /** Whether anything is still alight, so the loop knows to come back. */
  private paintFlares(state: Frame3DState, now: number): boolean {
    let alive = false;
    for (const flare of this.flares) {
      if (!flare.live) continue;
      if (flareAt(flare.at, now) <= 0) {
        flare.live = false;
        continue;
      }
      alive = true;
    }
    return alive && !state.reduced;
  }

  /**
   * The moment a hop landed, drawn on the file it landed on: a ring that
   * leaves the particle and goes out. It is the arrival itself and
   * nothing else — no timer starts it, an idle session produces none of
   * them, and a busy one produces exactly as many as the agents made.
   */
  private emitFlares(into: RingSet, state: Frame3DState, now: number): void {
    if (state.reduced) return;
    const layout = this.layout;
    if (!layout) return;

    for (const flare of this.flares) {
      if (!flare.live) continue;
      const strength = flareAt(flare.at, now);
      if (strength <= 0) continue;
      const node = layout.byId.get(flare.id);
      if (!node) continue;
      const i = node.index;
      const px = i === undefined ? 1 : this.perPixel(i);
      const out = 1 - strength;
      into.add(
        node.x,
        node.y,
        node.z,
        node.r + out * node.r * FLARE_REACH + px,
        px * 1.6,
        flare.color.r,
        flare.color.g,
        flare.color.b,
        strength * 0.6,
      );
    }
  }

  /* ---------------- helpers ---------------- */

  /**
   * World units per screen pixel at this particle's depth.
   *
   * The flat view strokes its rings at a constant number of *pixels*, so
   * a hairline stays a hairline however far in you zoom. World-sized
   * rings would fatten as the camera approached, which reads as the file
   * growing a border rather than as the camera moving.
   */
  private perPixel(index: number): number {
    const at = index * PROJECT_STRIDE;
    const onScreen = this.screen[at + 3];
    const node = this.layout?.nodes[index];
    if (!node || !(onScreen > 0.05)) return 1;
    return node.r / onScreen;
  }

  private perPixelOf(node: Node3D): number {
    const index = node.index;
    return index === undefined ? 1 : this.perPixel(index);
  }

  private layersById(
    workers: readonly WorkerLayer[],
  ): ReadonlyMap<string, WorkerLayer> {
    if (this.indexedFor === workers) return this.layerIndex;
    this.layerIndex.clear();
    for (const worker of workers) this.layerIndex.set(worker.id, worker);
    this.indexedFor = workers;
    return this.layerIndex;
  }
}

/* ------------------------------------------------------------------ *
 * Free functions
 * ------------------------------------------------------------------ */

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** How strongly an agent draws while another one has the focus. */
function focusAlpha(focus: string | null, agentId: string): number {
  if (focus === null || focus === agentId) return 1;
  return FOCUS_DIM;
}

function endOf(layout: Scene3D, end: string | Node3D): Node3D | undefined {
  return typeof end === "string" ? layout.byId.get(end) : end;
}

/** Leave a link's segments where they were: the layout must stay stable. */
function skipSegments(edges: LineSet, samples: number, dashed: boolean): void {
  edges.count = edges.count + drawnSegments(samples, dashed);
}

/** `out = a * b`, column-major, both 16 long. Allocates nothing. */
function multiply(
  a: ArrayLike<number>,
  b: ArrayLike<number>,
  out: Float32Array,
): void {
  for (let col = 0; col < 4; col += 1) {
    const c = col * 4;
    for (let row = 0; row < 4; row += 1) {
      out[c + row] =
        a[row] * b[c] +
        a[row + 4] * b[c + 1] +
        a[row + 8] * b[c + 2] +
        a[row + 12] * b[c + 3];
    }
  }
}
