import { repoChipLabel } from "../repoUrl";
import Box, { BoxRow } from "./Box";

interface Props {
  /**
   * The clone URL or `owner/name` — `repoChipLabel` shortens it. Empty when
   * nothing has been chosen yet. One spelling of `owner/name @ ref` on
   * every surface (HAR-84 P2-11), so the caller never formats it itself.
   */
  repo: string;
  /* Never call this `ref`: React reserves the prop name. */
  gitRef: string;
  gtMode: string;
  model: string;
}

const TIPS: readonly string[] = [
  "Paste a GitHub repo URL in your first message",
  "/spawn <task> runs a worker agent in parallel",
  "ctrl+g opens the code graph",
  "/resume picks up a previous session",
];

/**
 * The landing: a framed name, what it is pointed at, and four lines of how
 * to start. Nothing else — the input is the page.
 */
export default function TermBanner({ repo, gitRef, gtMode, model }: Props) {
  return (
    <>
      <div className="banner">
        <Box title=" GT Cloud Agent ">
          <BoxRow>
            <span className="banner-mark">▐▛</span> GT Cloud Agent — an agent
            with GroundTruth underneath
          </BoxRow>
          <BoxRow>
            <span className="dim">
              repo: {repo ? repoChipLabel(repo, gitRef || "main") : "none yet"} ·
              GT:{" "}
              {gtMode} · model: {model || "server default"}
            </span>
          </BoxRow>
        </Box>
      </div>

      <div className="tips">
        Tips for getting started:
        {TIPS.map((tip, i) => (
          <div key={tip}>
            {" "}
            {i + 1}. {tip}
          </div>
        ))}
      </div>
    </>
  );
}
