import { useMemo, useRef } from "react";
import type { DiffFile, SessionDiff } from "../api";
import { shortSha } from "../format";

interface Props {
  diff: SessionDiff | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
  onPickFile: (path: string | null) => void;
}

/** Claimed territory: which files changed, and exactly how. */
export default function ChangesPanel({
  diff,
  error,
  loading,
  onRefresh,
  onPickFile,
}: Props) {
  const sections = useMemo(() => splitPatch(diff?.patch ?? ""), [diff?.patch]);
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const files = diff?.files ?? [];

  const jump = (path: string) => {
    onPickFile(path);
    const match =
      sections.find((s) => s.path === path) ??
      sections.find((s) => s.path.endsWith(path) || path.endsWith(s.path));
    if (!match) return;
    sectionRefs.current[match.path]?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  };

  return (
    <>
      <div className="inst-head">
        <span className="cap">
          {files.length} file{files.length === 1 ? "" : "s"} changed
          {diff?.base_sha ? ` · base ${shortSha(diff.base_sha)}` : ""}
        </span>
        <button
          type="button"
          className="btn-text"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading ? "…" : "refresh"}
        </button>
      </div>

      {error && <div className="notice">{error}</div>}

      {!error && files.length === 0 && (
        <p className="inst-empty">No territory claimed yet.</p>
      )}

      {files.length > 0 && (
        <ul className="files">
          {files.map((file) => (
            <li key={file.path}>
              <button
                type="button"
                className="file-row"
                onClick={() => jump(file.path)}
              >
                <span className="file-mark" aria-hidden="true" />
                <span className="file-path" title={file.path}>
                  {file.path}
                </span>
                <span className="file-status cap">{status(file)}</span>
                <span className="file-counts">
                  <span className="add">+{file.additions}</span>
                  <span className="del">−{file.deletions}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {sections.length > 0 && (
        <div className="patch">
          {sections.map((section) => (
            <div
              key={section.path}
              className="patch-file"
              ref={(el) => {
                sectionRefs.current[section.path] = el;
              }}
            >
              {section.path && (
                <div className="patch-head">{section.path}</div>
              )}
              <pre>
                {section.lines.map((line, i) => (
                  <div key={i} className={`dl ${lineClass(line)}`}>
                    {line || " "}
                  </div>
                ))}
              </pre>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function status(file: DiffFile): string {
  switch (file.status) {
    case "added":
      return "added";
    case "deleted":
      return "deleted";
    case "modified":
      return "modified";
    default:
      return String(file.status);
  }
}

function lineClass(line: string): string {
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

export interface PatchSection {
  path: string;
  lines: string[];
}

const DIFF_HEADER = /^diff --git a\/(.+?) b\/(.+)$/;

/** Split a unified diff into per-file sections so files can be linked to. */
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
