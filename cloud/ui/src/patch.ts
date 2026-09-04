/* ------------------------------------------------------------------ *
 * Unified-diff reading. The session endpoint returns one patch for the
 * whole workspace; the inspector wants one file's worth of it.
 * ------------------------------------------------------------------ */

export interface PatchSection {
  path: string;
  lines: string[];
}

const DIFF_HEADER = /^diff --git a\/(.+?) b\/(.+)$/;

export function splitPatch(patch: string): PatchSection[] {
  if (!patch.trim()) return [];
  const sections: PatchSection[] = [];
  let current: PatchSection | null = null;

  for (const line of patch.split("\n")) {
    const header = DIFF_HEADER.exec(line);
    if (header) {
      current = { path: header[2] || header[1], lines: [line] };
      sections.push(current);
      continue;
    }
    if (!current) {
      // A patch without `diff --git` headers still renders as one section.
      current = { path: "", lines: [] };
      sections.push(current);
    }
    current.lines.push(line);
  }

  return sections;
}

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "ctx";

export function lineKind(line: string): DiffLineKind {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("diff --git") || line.startsWith("index ")) return "meta";
  if (line.startsWith("new file") || line.startsWith("deleted file")) {
    return "meta";
  }
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

/** One file's patch: the per-file field when the server sends it, else a slice. */
export function patchFor(
  path: string,
  wholePatch: string,
  filePatch: string | undefined,
): string[] {
  if (filePatch) return filePatch.replace(/\n+$/, "").split("\n");
  const sections = splitPatch(wholePatch);
  const match =
    sections.find((section) => section.path === path) ??
    sections.find(
      (section) =>
        section.path.endsWith(`/${path}`) || path.endsWith(`/${section.path}`),
    );
  return match ? match.lines : [];
}
