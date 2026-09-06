import {
  GT_MODE_HELP,
  GT_MODES,
  isGtMode,
  WALL_SECONDS_MAX,
  WALL_SECONDS_MIN,
} from "../api";
import { MODELS, STEP_LIMIT_MAX, STEP_LIMIT_MIN, type Prefs } from "../prefs";
import Box, { BoxRow } from "./Box";

const CUSTOM = "__custom__";

interface Props {
  prefs: Prefs;
  onChange: (next: Prefs) => void;
  onClose: () => void;
  /** One line under the fields, where these do not apply to this session. */
  note?: string;
}

/**
 * `/settings` — a form, drawn in the transcript where it was asked for.
 *
 * A popover is a web idea: it floats above the thing you were reading and
 * disappears when you look away. This is a block of the transcript, it
 * scrolls with everything else, and closing it leaves the line that opened
 * it behind.
 */
export default function TermSettings({ prefs, onChange, onClose, note }: Props) {
  const known = (MODELS as readonly string[]).includes(prefs.model);

  return (
    <div className="termform">
      <Box title=" settings " right=" esc to close ">
        <BoxRow>
          <label htmlFor="set-model">model</label>
          <select
            id="set-model"
            value={known ? prefs.model : CUSTOM}
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
            <option value={CUSTOM}>other…</option>
          </select>
        </BoxRow>

        {!known && (
          <BoxRow>
            <label htmlFor="set-model-id">model id</label>
            <input
              id="set-model-id"
              value={prefs.model}
              placeholder="provider/model-id"
              onChange={(e) => onChange({ ...prefs, model: e.target.value })}
            />
          </BoxRow>
        )}

        <BoxRow>
          <label htmlFor="set-gt">ground truth</label>
          <select
            id="set-gt"
            value={prefs.gtMode}
            onChange={(e) =>
              onChange({
                ...prefs,
                gtMode: isGtMode(e.target.value) ? e.target.value : prefs.gtMode,
              })
            }
          >
            {GT_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
          <span className="dim">— {GT_MODE_HELP[prefs.gtMode]}</span>
        </BoxRow>

        <BoxRow>
          <label htmlFor="set-steps">steps / turn</label>
          <input
            id="set-steps"
            type="number"
            min={STEP_LIMIT_MIN}
            max={STEP_LIMIT_MAX}
            value={prefs.stepLimit}
            onChange={(e) => {
              const n = Number.parseInt(e.target.value, 10);
              onChange({
                ...prefs,
                stepLimit: Number.isFinite(n) ? n : prefs.stepLimit,
              });
            }}
          />
        </BoxRow>

        <BoxRow>
          <label htmlFor="set-wall">seconds / turn</label>
          <input
            id="set-wall"
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
        </BoxRow>

        <BoxRow>
          <span className="dim">
            {note ?? "kept on this browser; every new session starts with these"}
          </span>
        </BoxRow>

        <BoxRow>
          <button type="button" className="bracket" onClick={onClose}>
            [close]
          </button>
        </BoxRow>
      </Box>
    </div>
  );
}
