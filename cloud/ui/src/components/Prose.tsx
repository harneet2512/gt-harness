import { useMemo } from "react";
import { splitFences } from "../fences";

/**
 * Deliberately not a markdown renderer. The surveyor speaks in prose with
 * the occasional fenced block, so whitespace is preserved and ``` fences
 * are lifted into <pre>. No HTML is ever injected.
 */
export default function Prose({ text }: { text: string }) {
  const blocks = useMemo(() => splitFences(text), [text]);

  if (blocks.length === 0) return null;

  return (
    <>
      {blocks.map((block, i) =>
        block.kind === "code" ? (
          <pre className="heard-code" key={i}>
            {block.lang && <span className="heard-lang cap">{block.lang}</span>}
            <code>{block.body}</code>
          </pre>
        ) : (
          <p className="heard-prose" key={i}>
            {block.body}
          </p>
        ),
      )}
    </>
  );
}
