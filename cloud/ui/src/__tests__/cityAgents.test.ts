import { describe, it, expect } from "vitest";
import { buildField } from "../graph";
import { projectAgents, occupancy } from "../cityAgents";
import { emptyWorker } from "../workers";
import type { WorkerTrail } from "../useGraphView";
const field = buildField(
  {
    base_sha: "fixture",
    gt: false,
    nodes: [
      { id: "src/a.ts", path: "src/a.ts", size: 40, lang: "ts", dir: "src" },
    ],
    edges: [],
  },
  new Set(),
);
const worker = emptyWorker("w");
worker.activity = [
  {
    key: "event-2",
    command: "pwd",
    output: "repository",
    returncode: 0,
    isError: false,
    gt: null,
    tool: null,
    files: [],
    answered: true,
  },
];
const trail = {
  id: "w",
  task: "Worker",
  status: "running",
  css: "#abcdef",
  isExternal: false,
  positionId: "src/a.ts",
} as WorkerTrail;
const workers = { byId: { w: worker }, order: ["w"] };
describe("agent projection", () => {
  it("resolves explicit external paths without inventing a location for missing files", () => {
    const external = { ...trail, isExternal: true };
    const state = {
      byId: {
        w: {
          ...worker,
          activity: [
            { ...worker.activity[0], tool: "Read", files: ["src/a.ts"] },
          ],
        },
      },
      order: ["w"],
    };
    expect(
      projectAgents(field, [], 0, false, [external], state, true)[1],
    ).toMatchObject({
      fileId: "src/a.ts",
      location: "explicit",
      activity: "reading",
    });
    state.byId.w.activity[0].files = ["deleted.ts"];
    expect(
      projectAgents(field, [], 0, false, [external], state, true)[1],
    ).toMatchObject({ fileId: null, location: "unknown" });
  });
  it("preserves outside-repository status", () => {
    expect(
      projectAgents(
        field,
        [],
        0,
        false,
        [{ ...trail, isExternal: true, outsideRepo: true }],
        workers,
        true,
      )[1],
    ).toMatchObject({ activity: "outside", fileId: null });
  });
  it("infers current worker locations from commands against the real file index", () => {
    const state = {
      byId: {
        w: {
          ...worker,
          activity: [{ ...worker.activity[0], command: "cat src/a.ts" }],
        },
      },
      order: ["w"],
    };
    expect(
      projectAgents(field, [], 0, false, [trail], state, true)[1],
    ).toMatchObject({
      fileId: "src/a.ts",
      location: "inferred",
      activity: "reading",
    });
  });
  it("does not keep a stale destination when the latest event has no file", () => {
    const a = projectAgents(field, [], 0, false, [trail], workers, true)[1];
    expect(a.fileId).toBeNull();
    expect(a.location).toBe("unknown");
    expect(a.sourceEvent).toBe("event-2");
  });
  it("does not certify successful generic commands", () => {
    const a = projectAgents(field, [], 0, false, [trail], workers, true)[1];
    expect(a.activity).toBe("working");
    expect(a.output).toBe("repository");
  });
  it("keeps worker live output outside primary replay", () => {
    expect(
      projectAgents(field, [], 0, false, [trail], workers, false).map(
        (a) => a.id,
      ),
    ).toEqual(["primary"]);
  });
  it("gives collocated identities stable unique offsets", () => {
    const a = projectAgents(field, [], 0, false, [trail], workers, true);
    expect(occupancy(a, "primary")).not.toBe(occupancy(a, "w"));
    expect(occupancy([...a].reverse(), "w")).toBe(occupancy(a, "w"));
  });
});
