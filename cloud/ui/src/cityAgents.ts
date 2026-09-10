import type { ParticleField } from "./graph";
import type { TrailStep } from "./trail";
import type { WorkerTrail } from "./useGraphView";
import type { WorkersState } from "./workers";
import { indexFiles, matchFiles, WRITES } from "./trail";
export interface AgentVisualState {
  id: string;
  label: string;
  color: string;
  activity:
    | "idle"
    | "reading"
    | "editing"
    | "verifying"
    | "working"
    | "outside";
  fileId: string | null;
  location: "explicit" | "inferred" | "unknown";
  sourceEvent: string | null;
  output: string;
  returncode: number | null;
  isError: boolean;
  command: string;
  running: boolean;
  changes: readonly string[];
}
export type CitySelection =
  | { kind: "file"; id: string }
  | { kind: "agent"; id: string }
  | null;
export function projectAgents(
  field: ParticleField,
  steps: readonly TrailStep[],
  cutoff: number,
  running: boolean,
  trails: readonly WorkerTrail[],
  workers: WorkersState,
  live: boolean,
): AgentVisualState[] {
  const last = steps[Math.min(cutoff, steps.length) - 1];
  const fileId =
    last?.files.map((p) => field.resolve.get(p)).find(Boolean) ?? null;
  const activity = (
    tool: string,
    active: boolean,
    command = "",
  ): AgentVisualState["activity"] =>
    !active
      ? "idle"
      : /^(Read|Glob|Grep)$/i.test(tool) ||
          /^(cat|head|tail|rg|grep|less)\s/.test(command)
        ? "reading"
        : /^(Edit|Write|apply_patch)$/i.test(tool) || WRITES.test(command)
          ? "editing"
          : /^(pytest|vitest|jest)\b|^(npm|pnpm|yarn|go) test\b/.test(command)
            ? "verifying"
            : "working";
  const result: AgentVisualState[] = [
    {
      id: "primary",
      label: "Primary agent",
      color: "#d97757",
      activity: activity("", running && live, last?.command ?? ""),
      fileId,
      location: fileId ? "inferred" : "unknown",
      sourceEvent: last ? String(last.eventId) : null,
      output: last?.output ?? "",
      returncode: last?.returncode ?? null,
      isError: last?.isError ?? false,
      command: last?.command ?? "",
      running: running && live,
      changes: [],
    },
  ];
  // Worker streams have independent clocks; do not show their live state as historical primary state.
  if (!live) return result;
  const index = indexFiles(
    [...field.resolve.keys()].map((path) => ({ path, size: 0 })),
  );
  for (const trail of trails) {
    const w = workers.byId[trail.id];
    const row = w?.activity[w.activity.length - 1];
    const resolved = row
      ? (trail.isExternal ? row.files : matchFiles(row.command, index))
          .map((p) => field.resolve.get(p))
          .find(Boolean)
      : null;
    const id = resolved ?? null;
    result.push({
      id: trail.id,
      label: w?.label || (trail.isExternal ? trail.kind ?? "External agent" : `Worker ${trail.no ?? trails.indexOf(trail)+1}`),
      color: trail.css,
      activity: trail.outsideRepo
        ? "outside"
        : activity(
            row?.tool ?? "",
            trail.status === "running",
            row?.command ?? "",
          ),
      fileId: id && field.byId.has(id) ? id : null,
      location: id ? (trail.isExternal ? "explicit" : "inferred") : "unknown",
      sourceEvent: row?.key ?? null,
      output: row?.output ?? w?.reply ?? "",
      returncode: row?.returncode ?? null,
      isError: row?.isError ?? false,
      command: row?.command ?? "",
      running: trail.status === "running",
      changes: w?.filesChanged ?? [],
    });
  }
  return result;
}
/** Stable identity ordering gives collocated vehicles separate landing positions. */
export function occupancy(
  agents: readonly AgentVisualState[],
  id: string,
): number {
  const agent = agents.find((a) => a.id === id);
  return agents
    .filter((a) => a.fileId === agent?.fileId)
    .map((a) => a.id)
    .sort()
    .indexOf(id);
}
