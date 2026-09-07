import { Component, lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import type { GraphViewProps } from "../graphProps";
import type { GraphMode } from "../prefs";
import { hasWebgl, NO_CHUNK, NO_WEBGL } from "../webgl";
import GraphCanvas from "./GraphCanvas";

/**
 * The 3D renderer, and everything it stands on, fetched only when
 * somebody asks for it. three.js and the octree solver are most of a
 * megabyte before compression; a session that stays flat should never
 * see a byte of it.
 */
const GraphCanvas3D = lazy(() => import("./GraphCanvas3D"));

interface Props extends GraphViewProps {
  mode: GraphMode;
}

/**
 * Which drawing of the field is on screen.
 *
 * Both take the same props and read the same `ParticleField`, so
 * switching is a swap of the renderer and nothing else: the session is
 * not reloaded, the trail is not recomputed, and the selection lives
 * above this component and never notices.
 *
 * Depth is a mode, not a requirement. A browser without WebGL, or a
 * chunk that would not load, gets the flat graph and one line saying so
 * — never a blank pane, and never an error.
 */
export default function GraphStage({ mode, ...props }: Props) {
  const [failed, setFailed] = useState(false);
  /* Asked before the import, so an unsupported browser does not download
     a renderer it cannot use. */
  const supported = useMemo(() => hasWebgl(), []);

  const wants3d = mode === "3d";
  const show3d = wants3d && supported && !failed;
  const note = !wants3d
    ? null
    : !supported
      ? NO_WEBGL
      : failed
        ? NO_CHUNK
        : null;

  return (
    <>
      {show3d ? (
        <Suspense
          fallback={
            <div className="graph">
              <div className="graph-empty">
                <span className="cap">loading the 3D view…</span>
              </div>
            </div>
          }
        >
          <Fallback onError={() => setFailed(true)}>
            <GraphCanvas3D {...props} />
          </Fallback>
        </Suspense>
      ) : (
        <GraphCanvas {...props} />
      )}
      {note && <p className="graph-note cap cap-muted">{note}</p>}
    </>
  );
}

interface FallbackProps {
  children: ReactNode;
  onError: () => void;
}

/**
 * The only thing standing between a WebGL context that would not come up
 * and a blank pane.
 *
 * It renders nothing on failure: the parent has already been told, and
 * on the next render it draws the flat view with the note underneath.
 * That is the whole design — a graph the reader can still read, plus one
 * sentence about what they are not getting.
 */
class Fallback extends Component<FallbackProps, { down: boolean }> {
  state = { down: false };

  static getDerivedStateFromError(): { down: boolean } {
    return { down: true };
  }

  componentDidCatch(): void {
    this.props.onError();
  }

  render(): ReactNode {
    return this.state.down ? null : this.props.children;
  }
}
