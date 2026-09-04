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

interface Props {
  onCreated: (session: Session) => void;
  onCancel: () => void;
}

/** Clone a repository into a fresh workspace: a new stretch of terrain. */
export default function NewExpeditionForm({ onCreated, onCancel }: Props) {
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
    const parsedLimit = Number.parseInt(stepLimit, 10);
    if (!Number.isFinite(parsedLimit) || parsedLimit < 1) {
      setError("Step limit must be a positive integer.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const session = await createSession({
        repo: repo.trim(),
        ref: ref.trim() || DEFAULT_REF,
        model,
        gt_mode: gtMode,
        step_limit: parsedLimit,
        temperature: DEFAULT_TEMPERATURE,
      });
      onCreated(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="new-expo" onSubmit={handleSubmit}>
      <h2 className="cap">new expedition</h2>

      <div className="field">
        <label className="cap" htmlFor="repo">
          repository
        </label>
        <input
          id="repo"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="https://github.com/owner/repo"
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
            style={{ marginTop: 8 }}
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
            value={stepLimit}
            onChange={(e) => setStepLimit(e.target.value)}
          />
        </div>
      </div>

      {error && <div className="notice">{error}</div>}

      <div className="new-expo-actions">
        <button type="submit" className="btn btn-orange" disabled={submitting}>
          {submitting ? "Surveying…" : "Begin survey"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
