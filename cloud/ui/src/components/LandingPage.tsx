import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createSession } from "../api";
import { combinePrompt } from "../launch";
import { loadPrefs, savePrefs, type Prefs } from "../prefs";
import { parseRepoRef, type RepoRef } from "../repoUrl";
import { useSessions } from "../useSessions";
import { helpText, type ParsedSlash } from "../slash";
import Composer from "./Composer";
import RepoChip from "./RepoChip";
import ResumeRail from "./ResumeRail";
import SettingsGear from "./SettingsGear";

const DEFAULT_REF = "main";
/* Not offered anywhere: the backend owns it and nothing here has an opinion. */
const TEMPERATURE = 0;

const ASK_REPO = "Which repository should I work in? Paste a GitHub URL.";
const SPAWN_SOON =
  "spawning worker agents is coming — the server side is being built";
const NO_SESSION =
  "There is no session yet. Send a task first; /stop, /close and /graph belong to a running one.";

interface Line {
  id: number;
  role: "user" | "agent" | "system";
  text: string;
}

/**
 * `/` — a prompt, and nothing else.
 *
 * You type the task. The repository is inferred from the message or from the
 * chip beneath it; the model and the budgets live behind the gear. Sending
 * creates the session and runs the first turn in one action.
 */
export default function LandingPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { sessions, error: listError } = useSessions();

  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  const [chosen, setChosen] = useState<RepoRef | null>(() => {
    const repo = params.get("repo");
    return repo ? { repo, ref: params.get("ref") } : null;
  });
  const [lines, setLines] = useState<Line[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusSignal, setFocusSignal] = useState(0);
  const [gearSignal, setGearSignal] = useState(0);
  const nextId = useRef(0);

  /* No explicit choice: work where you last worked. */
  const recent = sessions[0];
  const repo: RepoRef | null =
    chosen ?? (recent?.repo ? { repo: recent.repo, ref: recent.ref } : null);

  const push = (role: Line["role"], text: string) =>
    setLines((prev) => [...prev, { id: (nextId.current += 1), role, text }]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "k" || !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setFocusSignal((n) => n + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function onCommand({ command, arg }: ParsedSlash) {
    switch (command.name) {
      case "help":
        push("system", helpText());
        break;
      case "settings":
        setGearSignal((n) => n + 1);
        break;
      case "spawn":
        push("user", arg ? `/spawn ${arg}` : "/spawn");
        push("system", SPAWN_SOON);
        break;
      default:
        push("system", NO_SESSION);
        break;
    }
  }

  async function send(text: string): Promise<boolean> {
    const found = parseRepoRef(text);
    const target: RepoRef | null = found
      ? {
          repo: found.repo,
          ref: found.ref ?? (repo?.repo === found.repo ? repo.ref : null),
        }
      : repo;

    push("user", text);

    /* Nothing to clone and nothing named: ask, keep the intent, and let the
       next message — the one with the URL — carry both. */
    if (!target) {
      setPending(combinePrompt(pending, text));
      push("agent", ASK_REPO);
      setFocusSignal((n) => n + 1);
      return true;
    }

    const content = combinePrompt(pending, text);
    setError(null);
    try {
      const session = await createSession({
        repo: target.repo,
        ref: target.ref || DEFAULT_REF,
        model: prefs.model.trim() || "",
        gt_mode: prefs.gtMode,
        step_limit: prefs.stepLimit,
        temperature: TEMPERATURE,
        ...(prefs.wallSeconds ? { wall_seconds: prefs.wallSeconds } : {}),
      });
      setPending(null);
      navigate(`/sessions/${session.id}`, { state: { firstMessage: content } });
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      push("system", "The session could not be created. The prompt is still here.");
      return true;
    }
  }

  const chip = useMemo(
    () => (
      <span className="landing-foot-left">
        <RepoChip
          value={repo}
          sessions={sessions}
          onPick={(next) => setChosen(next)}
        />
      </span>
    ),
    [repo, sessions],
  );

  return (
    <div className="shell">
      <ResumeRail activeId={null} sessions={sessions} error={listError} />

      <main className="landing">
        <div className="landing-mid">
          <p className="landing-mark cap cap-muted">synapse</p>

          {lines.length > 0 && (
            <div className="landing-thread">
              {lines.map((line) =>
                line.role === "user" ? (
                  <p className="said" key={line.id}>
                    {line.text}
                  </p>
                ) : (
                  <p
                    className={`landing-say ${line.role === "system" ? "is-system" : ""}`}
                    key={line.id}
                  >
                    {line.text}
                  </p>
                ),
              )}
            </div>
          )}

          <Composer
            variant="landing"
            placeholder="What should I work on?"
            locked={false}
            lockedReason=""
            error={error}
            autoFocus
            focusSignal={focusSignal}
            onSend={send}
            onCommand={onCommand}
            footLeft={chip}
            footRight={
              <SettingsGear
                prefs={prefs}
                openSignal={gearSignal}
                onChange={(next) => {
                  setPrefs(next);
                  savePrefs(next);
                }}
              />
            }
          />
        </div>
      </main>
    </div>
  );
}
