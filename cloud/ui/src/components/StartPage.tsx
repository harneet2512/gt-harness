import { useNavigate } from "react-router-dom";
import NewSessionForm from "./NewSessionForm";
import SessionSwitcher from "./SessionSwitcher";

/**
 * `/` with nothing open. There is exactly one thing to do here, so the
 * page is that one thing — not the three-pane chrome drawing a toolbar, a
 * legend and a scrubber for a session that does not exist.
 */
export default function StartPage() {
  const navigate = useNavigate();

  return (
    <div className="start">
      <div className="start-card">
        <h1>Start a session</h1>
        <p className="start-sub">
          Clone a repository into a workspace, then watch the agent walk it.
        </p>

        <NewSessionForm
          title={null}
          onCreated={(session) => navigate(`/sessions/${session.id}`)}
        />

        <div className="start-foot">
          <span className="cap cap-muted">or open one you already have</span>
          <SessionSwitcher activeId={null} active={null} />
        </div>
      </div>
    </div>
  );
}
