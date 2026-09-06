import { useEffect, useRef, useState } from "react";
import { CONNECT_SECRET_NOTE, type ConnectBlock } from "../external";
import { Call, Cont, ContMore } from "./TermLine";

/** How long `[copied]` stays on the line before it goes back to `[copy]`. */
const COPIED_MS = 2000;

interface Props {
  /**
   * The registered agent's three exports and the one step that follows
   * them. `block.exports` is the **only** string in this app that carries
   * the token: it is built once, rendered once, and nothing else — no
   * note, no heading, no title attribute — repeats it.
   */
  block: ConnectBlock;
}

/**
 * `/connect` — what to run on your own machine to attach a local session:
 *
 *     ⏺ Connect(codex)
 *       ⎿  in the shell you run codex from:
 *       ⎿  export GT_CLOUD_ORIGIN='https://gt.example.com'
 *          export GT_CLOUD_AGENT_ID='a-9'
 *          export GT_CLOUD_AGENT_TOKEN='…'                          [copy]
 *       ⎿  then run the tailer — it follows the newest rollout by itself:
 *          python cloud/adapters/codex/gt_cloud_codex.py
 *       ⎿  the GT_CLOUD_AGENT_TOKEN above is a secret — …
 *       ⎿  docs/cloud/external-agents.md
 *
 * There is nothing to install: the adapters are in this repository and read
 * those three variables. Claude Code gets a hook rather than a command, so
 * its step is a sentence — printing a command it does not have was the
 * whole of the bug this replaces.
 */
export default function TermConnect({ block }: Props) {
  const [copied, setCopied] = useState(false);
  const lineRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  /* `navigator.clipboard` needs a secure context and is not there in every
     browser this runs in. The fallback selects this block — this one, not
     the first on the page — so a manual copy still works rather than the
     button failing silently. */
  const select = () => selectNode(lineRef.current);

  const copy = () => {
    try {
      const api = navigator.clipboard;
      if (api?.writeText) {
        void api.writeText(block.exports).then(() => setCopied(true), select);
        return;
      }
    } catch {
      /* fall through to the selection */
    }
    select();
  };

  return (
    <section className="connect" aria-label="connect an external agent">
      <Call tool="Connect" arg={block.kind} />
      <Cont tone="dim">in the shell you run {block.kind} from:</Cont>

      <Cont>
        <code className="connect-line" ref={lineRef}>
          {block.exports}
        </code>
        <span className="worker-actions">
          {"   "}
          <button type="button" className="bracket" onClick={copy}>
            [{copied ? "copied" : "copy"}]
          </button>
        </span>
      </Cont>

      <Cont tone="dim">{block.step}</Cont>
      {block.stepCommand !== "" && (
        <ContMore>
          <code className="connect-line">{block.stepCommand}</code>
        </ContMore>
      )}

      <ContMore>
        <span className="dim">{CONNECT_SECRET_NOTE}</span>
      </ContMore>
      <ContMore>
        <span className="dim">
          it shows up here as an agent the moment it sends its first step —{" "}
          {block.docs}
        </span>
      </ContMore>
    </section>
  );
}

/** Select the exports so a manual copy works where the API does not. */
function selectNode(node: HTMLElement | null): void {
  if (!node) return;
  try {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  } catch {
    /* nothing else to try; the block is on screen and can be selected */
  }
}
