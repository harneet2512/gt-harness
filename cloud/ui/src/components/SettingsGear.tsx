import { useEffect } from "react";
import { GT_MODE_HELP, GT_MODES, isGtMode, WALL_SECONDS_MAX, WALL_SECONDS_MIN } from "../api";
import {
  MODELS,
  STEP_LIMIT_MAX,
  STEP_LIMIT_MIN,
  type Prefs,
} from "../prefs";
import { usePopover } from "../usePopover";

const CUSTOM = "__custom__";

interface Props {
  prefs: Prefs;
  onChange: (next: Prefs) => void;
  /** Bumped by `/settings` to open the popover from the composer. */
  openSignal?: number;
  /** One line under the fields, where the settings do not apply here yet. */
  note?: string;
}

/**
 * Everything that used to be the landing form, behind one gear: the model,
 * how much ground truth the agent gets, and the two per-turn budgets.
 */
export default function SettingsGear({
  prefs,
  onChange,
  openSignal = 0,
  note,
}: Props) {
  const pop = usePopover();
  const { setOpen } = pop;

  useEffect(() => {
    if (openSignal > 0) setOpen(true);
  }, [openSignal, setOpen]);

  const known = (MODELS as readonly string[]).includes(prefs.model);
  const choice = known ? prefs.model : CUSTOM;

  return (
    <span className="gear" ref={pop.ref}>
      <button
        type="button"
        className={`btn-text gear-btn ${pop.open ? "is-on" : ""}`}
        aria-expanded={pop.open}
        aria-haspopup="dialog"
        aria-label="Settings"
        title="Model, ground truth and the per-turn budgets"
        onClick={pop.toggle}
      >
        <span aria-hidden="true">⚙</span>
      </button>

      {pop.open && (
        <div className="pop pop-gear" role="dialog" aria-label="Settings">
          <div className="field">
            <label className="cap" htmlFor="gear-model">
              model
            </label>
            <select
              id="gear-model"
              value={choice}
              onChange={(e) =>
                onChange({
                  ...prefs,
                  model: e.target.value === CUSTOM ? "" : e.target.value,
                })
              }
            >
              {MODELS.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
              <option value={CUSTOM}>Other…</option>
            </select>
            {!known && (
              <input
                className="gear-custom"
                value={prefs.model}
                autoFocus
                placeholder="provider/model-id"
                aria-label="Model identifier"
                onChange={(e) => onChange({ ...prefs, model: e.target.value })}
              />
            )}
          </div>

          <div className="field">
            <label className="cap" htmlFor="gear-gt">
              ground truth
            </label>
            <select
              id="gear-gt"
              value={prefs.gtMode}
              aria-describedby="gear-gt-help"
              onChange={(e) =>
                onChange({
                  ...prefs,
                  gtMode: isGtMode(e.target.value) ? e.target.value : prefs.gtMode,
                })
              }
            >
              {GT_MODES.map((mode) => (
                <option key={mode} value={mode} title={GT_MODE_HELP[mode]}>
                  {mode}
                </option>
              ))}
            </select>
            <p className="field-hint" id="gear-gt-help">
              <strong>{prefs.gtMode}</strong> — {GT_MODE_HELP[prefs.gtMode]}
            </p>
          </div>

          <div className="field-row">
            <div className="field">
              <label className="cap" htmlFor="gear-steps">
                steps per turn
              </label>
              <input
                id="gear-steps"
                type="number"
                min={STEP_LIMIT_MIN}
                max={STEP_LIMIT_MAX}
                step={1}
                value={prefs.stepLimit}
                onChange={(e) => {
                  const n = Number.parseInt(e.target.value, 10);
                  onChange({
                    ...prefs,
                    stepLimit: Number.isFinite(n) ? n : prefs.stepLimit,
                  });
                }}
              />
            </div>
            <div className="field">
              <label className="cap" htmlFor="gear-wall">
                seconds per turn
              </label>
              <input
                id="gear-wall"
                type="number"
                min={WALL_SECONDS_MIN}
                max={WALL_SECONDS_MAX}
                step={30}
                placeholder="server default"
                value={prefs.wallSeconds ?? ""}
                onChange={(e) => {
                  const raw = e.target.value.trim();
                  const n = Number.parseInt(raw, 10);
                  onChange({
                    ...prefs,
                    wallSeconds: raw === "" || !Number.isFinite(n) ? null : n,
                  });
                }}
              />
            </div>
          </div>

          <p className="field-hint">
            {note ??
              "Kept on this browser. Every new session starts with these."}
          </p>
        </div>
      )}
    </span>
  );
}
