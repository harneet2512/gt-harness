import { useState } from "react";
import { exitNote } from "../format";
import { Cont, ContMore } from "./TermLine";

/** Six lines is what a terminal shows before it starts costing you the page. */
export const CLIP_LINES = 6;

interface Props {
  output: string;
  returncode: number | null;
  isError: boolean;
  clip?: number;
}

/**
 * Command output as continuation lines:
 *
 *     ⎿  first line
 *        second line
 *        … +37 lines (click to expand)
 *
 * A failure leads with `Error:` in red, because that is the line the reader
 * is looking for and scrolling past six lines of stdout to find it is not
 * reading, it is searching.
 */
export default function TermOutput({
  output,
  returncode,
  isError,
  clip = CLIP_LINES,
}: Props) {
  const [open, setOpen] = useState(false);

  const lines = output.length > 0 ? output.replace(/\n+$/, "").split("\n") : [];
  const failed = returncode !== null && returncode !== 0;
  const bad = failed || isError;
  const note = failed ? exitNote(returncode) : null;

  if (lines.length === 0 && !failed) return null;

  const overflows = lines.length > clip;
  const shown = open || !overflows ? lines : lines.slice(0, clip);
  const tone = bad ? "error" : "";

  return (
    <>
      {lines.length === 0 ? (
        <Cont tone={tone}>{bad ? "Error: (no output)" : "(no output)"}</Cont>
      ) : (
        shown.map((line, i) =>
          i === 0 ? (
            <Cont key={i} tone={tone}>
              {bad ? `Error: ${line}` : line}
            </Cont>
          ) : (
            <ContMore key={i}>
              <span className={bad ? "dim" : undefined}>{line}</span>
            </ContMore>
          ),
        )
      )}

      {overflows && (
        <ContMore>
          <button
            type="button"
            className="cont-more"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(!open);
            }}
          >
            {open
              ? "… show less"
              : `… +${lines.length - clip} lines (click to expand)`}
          </button>
        </ContMore>
      )}

      {failed && (
        <ContMore>
          <span className="dim">
            exit {returncode}
            {note ? ` — ${note}` : ""}
          </span>
        </ContMore>
      )}
    </>
  );
}
