import { useState } from "react";
import {
  createSession,
  WALL_SECONDS_MAX,
  WALL_SECONDS_MIN,
  type GtMode,
  type Session,
} from "../api";

const MODELS = [
  "google/gemma-4-31b-it:free",
  "nvidia/nemotron-3-super-120b-a12b:free",
  "minimax/minimax-m3:free",
  "deepseek/deepseek-v4-flash",
] as const;

const CUSTOM_MODEL = "__custom__";
const GT_MODES: readonly GtMode[] = ["off", "advisory", "engine"];

const DEFAULT_REF = "main";
const DEFAULT_STEP_LIMIT = 60;
/** Not exposed in the form; the backend defaults it, we still send the field. */
const DEFAULT_TEMPERATURE = 0;

/** `https://host/owner/repo`, which is as much as the client can know. */
const REPO_URL = /^https:\/\/[^\s/]+\/[^\s/]+\/[^\s/]+\/?$/;

/**
 * Values to open the form with. Used to start a fresh session on the repo a
 * closed one was working — retyping the URL, the ref and the model is the
 * whole cost of an expired workspace, and it is avoidable.
 */
export interface NewSessionSeed {
  repo?: string;
  ref?: string;
  model?: string;
  gtMode?: string;
  stepLimit?: number;
  wallSeconds?: number;
}

interface Props {
  onCreated: (session: Session) => void;
  /** Omitted where there is nothing to go back to, e.g. the empty state. */
  onCancel?: () => void;
  /** The form's own heading; pass null where the page already has one. */
  title?: string | null;
  /** Pre-filled values; anything omitted keeps the form's own default. */
  seed?: NewSessionSeed;
}

/** A model the picker does not list is still a model: keep it, as "Other…". */
function seedModel(model: string | undefined): [string, string] {
  if (!model) return [MODELS[0], ""];
  return (MODELS as readonly string[]).includes(model)
    ? [model, ""]
    : [CUSTOM_MODEL, model];
}

/** Clone a repository into a fresh workspace. */
export default function NewSessionForm({
  onCreated,
  onCancel,
  title = "new session",
  seed,
}: Props) {
  const [seedChoice, seedCustom] = seedModel(seed?.model);
  const [repo, setRepo] = useState(seed?.repo ?? "");
  const [ref, setRef] = useState(seed?.ref || DEFAULT_REF);
  const [modelChoice, setModelChoice] = useState<string>(seedChoice);
  const [customModel, setCustomModel] = useState(seedCustom);
  const [gtMode, setGtMode] = useState<string>(seed?.gtMode ?? GT_MODES[0]);
  const [stepLimit, setStepLimit] = useState(
    String(seed?.stepLimit ?? DEFAULT_STEP_LIMIT),
  );
  /* Blank on purpose: the server owns the default (TURN_WALL_SECONDS),
     and repeating it here is how the two drift apart. */
  const [wallSeconds, setWallSeconds] = useState(
    seed?.wallSeconds ? String(seed.wallSeconds) : "",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isCustom = modelChoice === CUSTOM_MODEL;
  const model = isCustom ? customModel.trim() : modelChoice;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!model) {
      setError("Enter a model identifier.");
      return;
    }
    // The step limit is `type=number required min=1`, so the browser has
    // already refused anything this could re-check.
    const parsedLimit = Number.parseInt(stepLimit, 10);

    if (!REPO_URL.test(repo.trim())) {
      setError(
        "Repository must be an https:// URL of the form https://github.com/owner/repo.",
      );
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const parsedWall = Number.parseInt(wallSeconds, 10);
      onCreated(
        await createSession({
          repo: repo.trim(),
          ref: ref.trim() || DEFAULT_REF,
          model,
          gt_mode: gtMode,
          step_limit: parsedLimit,
          temperature: DEFAULT_TEMPERATURE,
          ...(Number.isFinite(parsedWall) && wallSeconds.trim() !== ""
            ? { wall_seconds: parsedWall }
            : {}),
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="newsess" onSubmit={handleSubmit}>
      {title && <h2 className="cap">{title}</h2>}

      <div className="field">
        <label className="cap" htmlFor="repo">
          repository
        </label>
        <input
          id="repo"
          type="url"
          inputMode="url"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="https://github.com/owner/repo"
          title="An https:// URL, like https://github.com/owner/repo"
          required
          autoFocus
        />
      </div>

      <div className="field">
        <label className="cap" htmlFor="ref">
          ref — branch, tag or sha
        </label>
        <input
          id="ref"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder={DEFAULT_REF}
        />
      </div>

      <div className="field">
        <label className="cap" htmlFor="model">
          model
        </label>
        <select
          id="model"
          value={modelChoice}
          onChange={(e) => setModelChoice(e.target.value)}
        >
          {MODELS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
          <option value={CUSTOM_MODEL}>Other…</option>
        </select>
        {isCustom && (
          <input
            className="newsess-custom"
            value={customModel}
            onChange={(e) => setCustomModel(e.target.value)}
            placeholder="provider/model-id"
            required
          />
        )}
      </div>

      <div className="field-row">
        <div className="field">
          <label className="cap" htmlFor="gt">
            ground truth
          </label>
          <select
            id="gt"
            value={gtMode}
            onChange={(e) => setGtMode(e.target.value)}
          >
            {GT_MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="cap" htmlFor="steps">
            steps per turn
          </label>
          <input
            id="steps"
            type="number"
            min={1}
            step={1}
            required
            value={stepLimit}
            onChange={(e) => setStepLimit(e.target.value)}
          />
        </div>
      </div>

      <div className="field">
        <label className="cap" htmlFor="wall">
          time per turn — seconds, blank for the server default
        </label>
        <input
          id="wall"
          type="number"
          min={WALL_SECONDS_MIN}
          max={WALL_SECONDS_MAX}
          step={30}
          placeholder="server default"
          title={`Between ${WALL_SECONDS_MIN} and ${WALL_SECONDS_MAX} seconds`}
          value={wallSeconds}
          onChange={(e) => setWallSeconds(e.target.value)}
        />
      </div>

      {error && <div className="notice">{error}</div>}

      <div className="newsess-actions">
        <button type="submit" className="btn btn-orange" disabled={submitting}>
          {submitting ? "Cloning…" : "Start session"}
        </button>
        {onCancel && (
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
