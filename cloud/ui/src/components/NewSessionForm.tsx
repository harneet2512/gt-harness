import { useState } from "react";
import { createSession, type GtMode, type Session } from "../api";

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

interface Props {
  onCreated: (session: Session) => void;
  /** Omitted where there is nothing to go back to, e.g. the empty state. */
  onCancel?: () => void;
  /** The form's own heading; pass null where the page already has one. */
  title?: string | null;
}

/** Clone a repository into a fresh workspace. */
export default function NewSessionForm({
  onCreated,
  onCancel,
  title = "new session",
}: Props) {
  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState(DEFAULT_REF);
  const [modelChoice, setModelChoice] = useState<string>(MODELS[0]);
  const [customModel, setCustomModel] = useState("");
  const [gtMode, setGtMode] = useState<string>(GT_MODES[0]);
  const [stepLimit, setStepLimit] = useState(String(DEFAULT_STEP_LIMIT));
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
      onCreated(
        await createSession({
          repo: repo.trim(),
          ref: ref.trim() || DEFAULT_REF,
          model,
          gt_mode: gtMode,
          step_limit: parsedLimit,
          temperature: DEFAULT_TEMPERATURE,
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
