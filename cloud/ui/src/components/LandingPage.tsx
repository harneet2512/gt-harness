import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createSession } from "../api";
import { combinePrompt, createAndStart } from "../launch";
import { refreshPalette } from "../palette";
import { loadPrefs, savePrefs, type Prefs } from "../prefs";
import { parseRepoRef, repoChipLabel, type RepoRef } from "../repoUrl";
import { helpText, parseSpawn, type ParsedSlash } from "../slash";
import { applyTheme, loadTheme, saveTheme, themeFromArg, type Theme } from "../theme";
import { useSessions } from "../useSessions";
import Composer from "./Composer";
import ResumePicker from "./ResumePicker";
import TermBanner from "./TermBanner";
import TermSettings from "./TermSettings";
import { Cont } from "./TermLine";

const DEFAULT_REF = "main";
/* Not offered anywhere: the backend owns it and nothing here has an opinion. */
const TEMPERATURE = 0;

const ASK_REPO = "Which repository should I work in? Paste a GitHub URL.";
const ASK_REPO_SPAWN =
  "Which repository should the workers clone? Paste a GitHub URL, then " +
  "send the /spawn lines again.";
const NO_SESSION =
  "There is no session yet. Send a task first; /stop, /close and /graph " +
  "belong to a running one.";

interface Line {
  id: number;
  role: "user" | "agent" | "system";
  text: string;
}

/**
 * `/` — a banner, four tips, and a prompt.
 *
 * You type the task. The repository is inferred from the message or from
 * the last session; the model and the budgets live behind `/settings`.
 * Sending creates the session and runs the first turn in one call.
 */
export default function LandingPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { sessions, error: listError } = useSessions();

  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  const [theme, setTheme] = useState<Theme>(() => loadTheme());
  /* `/?repo=…&ref=…` — the link a closed session offers, so "start again on
     this repo" lands on a prompt that is already pointed at it. */
  const chosen = useMemo<RepoRef | null>(() => {
    const repo = params.get("repo");
    return repo ? { repo, ref: params.get("ref") } : null;
  }, [params]);
  const [lines, setLines] = useState<Line[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusSignal, setFocusSignal] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);
  const nextId = useRef(0);

  /* No explicit choice: work where you last worked. */
  const recent = sessions.find((session) => !session.parent_id) ?? sessions[0];
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

  function onCommand({ command, raw }: ParsedSlash) {
    /* Every command is echoed before whatever it did, exactly as the
       session page echoes it: an output block with no `>` line above it is
       output from nowhere (HAR-84 P2-6). */
    push("user", raw);
    switch (command.name) {
      case "help":
        push("system", helpText());
        break;
      case "settings":
        setSettingsOpen(true);
        break;
      case "theme": {
        const next = themeFromArg(raw.replace(/^\/theme\s*/, ""), theme);
        setTheme(next);
        applyTheme(next);
        saveTheme(next);
        refreshPalette();
        push("system", `theme: ${next}`);
        break;
      }
      case "resume":
        setResumeOpen(true);
        break;
      case "spawn":
        void spawnHere(raw);
        break;
      default:
        push("system", NO_SESSION);
        break;
    }
  }

  /**
   * `/spawn` before there is anything to spawn from. The session is created
   * on the repository the page already knows about, and the tasks travel
   * with the navigation: the server refuses a spawn while a session is
   * still `creating`, so the session page sends them once it is `idle`.
   */
  async function spawnHere(raw: string) {
    const draft = parseSpawn(raw);
    if (draft.error) {
      push("system", draft.error);
      return;
    }
    if (!repo) {
      push("agent", ASK_REPO_SPAWN);
      setFocusSignal((n) => n + 1);
      return;
    }
    setError(null);
    try {
      const session = await createSession(sessionBody(repo));
      navigate(`/sessions/${session.id}`, { state: { spawnTasks: draft.tasks } });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      push("system", "The session could not be created.");
    }
  }

  /** Everything a session is created with, bar the first message. */
  function sessionBody(target: RepoRef) {
    return {
      repo: target.repo,
      ref: target.ref || DEFAULT_REF,
      model: prefs.model.trim() || "",
      gt_mode: prefs.gtMode,
      step_limit: prefs.stepLimit,
      temperature: TEMPERATURE,
      ...(prefs.wallSeconds ? { wall_seconds: prefs.wallSeconds } : {}),
    };
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
      /* One call: `first_message` starts the first turn as soon as the
         workspace is ready. The create → poll → POST path is still there and
         is used only if the server will not take the field. */
      const started = await createAndStart(
        sessionBody(target),
        content,
        createSession,
      );
      setPending(null);
      navigate(`/sessions/${started.session.id}`, {
        state: { firstMessage: content, alreadySent: started.sent },
      });
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      push("system", "The session could not be created. The prompt is still here.");
      return true;
    }
  }

  return (
    <div className="shell">
      <main className="landing">
        <div className="landing-mid">
          <TermBanner
            repo={repo?.repo ?? ""}
            gitRef={repo?.ref ?? DEFAULT_REF}
            gtMode={prefs.gtMode}
            model={prefs.model}
          />

          {listError && <Cont tone="error">{listError}</Cont>}

          {lines.length > 0 && (
            <div className="landing-thread">
              {lines.map((line) =>
                line.role === "user" ? (
                  <p className="termsaid" key={line.id}>
                    <span className="termsaid-mark" aria-hidden="true">
                      &gt;
                    </span>
                    <span>{line.text}</span>
                  </p>
                ) : (
                  <Cont key={line.id} tone={line.role === "system" ? "dim" : ""}>
                    {line.text}
                  </Cont>
                ),
              )}
            </div>
          )}

          {settingsOpen && (
            <TermSettings
              prefs={prefs}
              onChange={(next) => {
                setPrefs(next);
                savePrefs(next);
              }}
              onClose={() => setSettingsOpen(false)}
            />
          )}

          <Composer
            variant="landing"
            placeholder="what should I work on?"
            locked={false}
            lockedReason=""
            error={error}
            autoFocus
            focusSignal={focusSignal}
            status={
              repo
                ? `${repoChipLabel(repo.repo, repo.ref ?? DEFAULT_REF)} · ${
                    prefs.model
                  } · GT ${prefs.gtMode}`
                : `no repo yet · ${prefs.model} · GT ${prefs.gtMode}`
            }
            onSend={send}
            onCommand={onCommand}
          />
        </div>
      </main>

      {resumeOpen && (
        <ResumePicker
          sessions={sessions}
          activeId={null}
          onClose={() => setResumeOpen(false)}
        />
      )}
    </div>
  );
}
