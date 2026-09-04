import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { getMe, LOGIN_URL, type User } from "./api";
import SurveyPage from "./components/SurveyPage";

type AuthState =
  | { phase: "loading" }
  | { phase: "anonymous" }
  | { phase: "signed-in"; user: User };

function SignIn() {
  return (
    <div className="signin">
      <div className="signin-card">
        <h1>GT Cloud Agent</h1>
        <p>Survey your codebase with an agent you can watch.</p>
        <button
          type="button"
          className="btn"
          onClick={() => {
            // Full-page navigation: the OAuth redirect must leave the SPA.
            window.location.href = LOGIN_URL;
          }}
        >
          Continue with GitHub
        </button>
      </div>
    </div>
  );
}

/** Keyed by session id so switching remounts (fresh SSE + thread state). */
function SessionRoute() {
  const { id } = useParams<{ id: string }>();
  return <SurveyPage key={id} />;
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
    return <div className="booting">Checking the radio…</div>;
  }
  if (auth.phase === "anonymous") {
    return <SignIn />;
  }

  return (
    <Routes>
      <Route path="/" element={<SurveyPage />} />
      <Route path="/sessions/:id" element={<SessionRoute />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
