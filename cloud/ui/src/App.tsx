import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { getMe, LOGIN_URL, type User } from "./api";
import { BUILD_SHA } from "./build";
import { Icon } from "./components/WorkspaceChrome";
import LandingPage from "./components/LandingPage";
import SynapsePage from "./components/SynapsePage";

type Phase =
  | { phase: "loading" }
  | { phase: "anonymous"; notice: string | null }
  | { phase: "signed-in"; user: User };

/**
 * The one screen the terminal re-skin had not reached: it still said
 * "SYNAPSE" over a rounded card while every other surface was box-drawn
 * (HAR-84 P2-7). It is the same box, the same `>` prompt, and it carries
 * the server's own reason when there is one — an expired sign-in reads as
 * the app breaking otherwise.
 */
function SignIn({ notice }: { notice: string | null }) {
  return (
    <div className="signin">
      <div className="signin-card">
        <div className="signin-brand">GT</div><h1>Your code. A shared perspective.</h1><p className="signin-description">Give your agents a task. Follow their work, inspect every change, and keep the whole repository in view.</p>

        {notice && (
          <div className="cont is-error signin-notice">
            <span className="cont-mark" aria-hidden="true">
              ⎿
            </span>
            <span className="cont-body">{notice}</span>
          </div>
        )}

        <p className="signin-prompt">
          <button
            type="button"
            className="signin-button"
            onClick={() => {
              // Full-page navigation: the OAuth redirect must leave the SPA.
              window.location.href = LOGIN_URL;
            }}
          >
            Continue with GitHub <Icon name="branch"/>
          </button>
        </p>

        <p className="build-stamp mono">build {BUILD_SHA}</p>
      </div>
    </div>
  );
}

/** Keyed by session id so switching remounts (fresh SSE + thread state). */
function SessionRoute() {
  const { id } = useParams<{ id: string }>();
  return <SynapsePage key={id} />;
}

export default function App() {
  const [auth, setAuth] = useState<Phase>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then(({ user, notice }) => {
        if (cancelled) return;
        setAuth(user ? { phase: "signed-in", user } : { phase: "anonymous", notice });
      })
      .catch(() => {
        if (!cancelled) setAuth({ phase: "anonymous", notice: null });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (auth.phase === "loading") return <div className="booting">…</div>;
  if (auth.phase === "anonymous") return <SignIn notice={auth.notice} />;

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/sessions/:id" element={<SessionRoute />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
