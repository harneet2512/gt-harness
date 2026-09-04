import { useEffect, useState } from "react";
import { Route, Routes, useParams } from "react-router-dom";
import { getMe, LOGIN_URL, type User } from "./api";
import Dashboard from "./components/Dashboard";
import Workspace from "./components/Workspace";

type AuthState =
  | { phase: "loading" }
  | { phase: "anonymous" }
  | { phase: "signed-in"; user: User };

function SignIn() {
  return (
    <div className="signin">
      <h1>GT Cloud Agent</h1>
      <p>Internal harness. Access is restricted to allow-listed GitHub logins.</p>
      <button
        className="btn-primary"
        onClick={() => {
          // Full-page navigation: the OAuth redirect must leave the SPA.
          window.location.href = LOGIN_URL;
        }}
      >
        Sign in with GitHub
      </button>
    </div>
  );
}

/** Keyed by session id so switching sessions remounts (fresh SSE + dedupe state). */
function WorkspaceRoute() {
  const { id } = useParams<{ id: string }>();
  return <Workspace key={id} />;
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((user) => {
        if (cancelled) return;
        setAuth(user ? { phase: "signed-in", user } : { phase: "anonymous" });
      })
      .catch(() => {
        if (!cancelled) setAuth({ phase: "anonymous" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (auth.phase === "loading") {
    return <div className="empty-state">Checking session...</div>;
  }
  if (auth.phase === "anonymous") {
    return <SignIn />;
  }

  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/sessions/:id" element={<WorkspaceRoute />} />
    </Routes>
  );
}
