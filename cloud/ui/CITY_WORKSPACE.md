# Live Cloud Agent implementation record

## Current review - layout v3, 2026-09-08

This section supersedes earlier implementation and browser status below, which is retained as dated history. The product remains repository-driven through the existing session graph and SSE state. Building dimensions use actual byte sizes; a regression test verifies metadata updates change massing while retaining every plot. CityLayout now types its file lookup as CityPlot, preserving architectural metadata through selection.

The new pass adds orthogonal building groups, stepped terrain elevation, six beveled archetypes with roof coping, continuous studio ground, restrained site lines and physical walkways. The viewport extends behind its header, with camera fitting against architecture bounds. Surveyors target approximately 40 screen pixels and use actual archetype roof anchors for scanning and editing accents. The inspector portrait renders the same procedural vehicle. Narrow inspectors have opaque backgrounds.

Three.js built-in GTAO supplies contact definition with transparent overlays excluded from its normal/depth pass. Degradation reduces pixel ratio, then AO, shadows and secondary labels. Renderer, postprocessing and portrait resources have disposal paths; a long-lived resource stress test remains outstanding.

Browser-only review used 125 actual local source files and three explicitly simulated agents. Screenshots identify the simulation; it is not a product fixture route or live deployment. A dispatched SSE event changed the inspector file and moved the primary vehicle through an intermediate position to its new roof while all building plots remained identical. Roof raycasting tests cover all six archetypes, including split-volume roofs. Light/dark 1440x900 and dark 1728x1117, 2560x1440, 900x900 and 390x844 captures were produced; desktop and narrow captures were visually inspected. Terrain shape and skyline richness still differ from the supplied references, so visual acceptance remains open.

The active 125-file, three-agent scene recorded approximately 13.3ms median and 13.5ms p95 frame intervals over three seconds on an NVIDIA RTX 2060, headless Edge, DPR 1, with AO enabled. This is browser frame-interval evidence, not GPU timing or the 1,000-file integrated-GPU acceptance target. A sampled renderer reported 25 geometries and 12 textures; this is not a resource-lifetime proof.

Current verification: 370 UI tests pass across 15 files. Browser checks against the ordinary local mock API confirm both themes, a 2D/3D round trip, no page errors, and one animation callback over a 1.5-second reduced-motion idle window. The local preview is http://127.0.0.1:5173/sessions/sqa and contains the existing 29-file mock session. Requested acook2825 deployment remains blocked on access; authenticated live operation, reconnect/context-loss browser exercises, prolonged resource checks and integrated-GPU performance remain unverified. No benchmark dispatch occurred and no benchmark findings are closed by these results.

## Browser review addendum - 2026-09-08

This addendum supersedes the browser-unavailable status below. At the user's direction, local Edge was driven headlessly through Playwright installed only in the ignored QA workspace. Full application screenshots were captured and visually inspected at 1440x900 in both themes, 1728x1117 in light, and 900x900. The original preview was showing sign-in because its API proxy failed; the stale Vite processes were replaced. The checked preview is `http://127.0.0.1:5173/sessions/sqa`, backed by the existing mock API, not a deployed live session.

Visual fixes: tighter framing against actual contours; activity dock positioned beside the architecture instead of expanding the camera's vertical bounds; size-appropriate district reserves; stable composition after an empty loading response; balanced initial rows; non-intersecting terrace layers with distinct side shading; bounded byte-size skyline variation; approximately 32px Surveyors with separate dock positions; selected-file label priority; and consistent sans-serif typography in dark mode. The existing 29-file preview now occupies approximately 70% of viewport width by visual inspection. This is not a claim that all repository shapes have the same screen occupancy or that the reference is matched perfectly.

The denser review used 125 actual local source files with real byte sizes, supplied through browser-only request interception and marked VISUAL TEST on screenshots. Its session data remained mocked. No fixture route, fabricated metadata, new session connection or provider call was added to the product. QA captures and scripts are under `D:/gt-harness/.tmp/city-qa/`; `checked-preview-light.png` and `checked-preview-dark.png` show the actual unmodified local API preview. `after-light-1440.png`, `after-dark-1440.png`, `after-hover.png` and `after-selection.png` show the labeled source sample.

Browser checks confirmed that raycast hover and click identify the same file in the inspector, both themes render, and switching 3D to 2D and back succeeds with no page errors. The reduced-motion idle sample recorded one animation callback over 1.5 seconds; no claim of measured integrated-GPU 60 FPS or complete resource-lifetime validation follows from this. Active scanning/travel against authenticated live agents, 2560x1440, context loss and long-lived GPU resource measurements remain open.

Latest checks: build passed; 368 UI tests passed in 15 files; whitespace check passed. Renderer chunk: 608.42 kB, 158.06 kB gzip, with the existing Vite size advisory. Deployment to acook2825 still requires access. Visual quality remains subject to review rather than a declared 10/10 score.

Updated 2026-09-08. UI baseline: `6957e4ed3b1865359675cf8de5a2bc29382f13c5`. Status: implementation candidate. Authenticated deployment, browser acceptance and hardware performance remain unverified.

## Product

The full Cloud Agent shell now includes repository navigation, agent roster, task composer, contextual file/agent inspector and a resizable output drawer. Existing send, steer, stop, worker focus/apply, session navigation, replay, diffs and receipts remain connected. `useSessionData`, SSE ingestion and `useGraphView` retain data ownership; no parallel event store or connection was added. The isolated specimen is removed from the application.

City layout v2 uses six beveled archetypes, five irregular contour terraces, bounded byte-size massing and camera-aligned initial packing. Existing plots survive edits; overflow does not move retained plots. Four session layouts are cached. Deleted-file tombstones persist until eviction; long-lived tombstone memory is not measured. All API-returned files are retained; server truncation remains a limitation.

The imperative lazy renderer uses instancing, depth testing, sRGB/ACES output, studio hemisphere/key/fill lighting, bounded soft shadows and restrained district tints. Light is the new preference default; explicit saved preferences remain respected. FOV is 38 degrees, fit uses projected bounds, automatic orbit is removed, and file framing retains neighborhood context. Contextual relations are sparse and curved; labels are collision-filtered and capped at 12.

The shared procedural Surveyor measures 18 x 12.5 x 5.5, with four enclosed rotors, capsule, integrated arms, underside scanner, forward sensor, two rear fins and one emitter. Regenerate `public/gt-surveyor-v2.svg` with `node --experimental-strip-types scripts/surveyor-sheet.mjs`. Offline sheet review is not WebGL acceptance.

Agents resolve real events against repository paths; unknown locations use an activity dock. Primary replay excludes independent live worker state. Generic command success never becomes verification success. LOC and symbol counts remain unavailable. Unapplied worker diffs open the worker session instead of presenting parent changes. Output retains actual event timestamps and supports agent filtering and incremental history. Reduced motion and explicit follow are preserved; user camera input cancels automatic framing.

## Runtime and target

The cloud server, sandbox, adapters, producer, devcontainer and cloud CI deleted by commit `c464bc57` were restored from its parent `37489dc5ff06239211acc2728483a11a7704dd95`. Server import succeeds. The original FastAPI, SQLite, sandbox and nginx architecture is retained.

A dedicated Codespace received the runtime source and UI overlay but no application deployment or provider call occurred. The user then directed deployment to acook2825. Both temporary Codespaces under the earlier account have been stopped; the existing benchmark workspace was only observed. Requested-account access remains unresolved. Local port 8000 is an existing mock server and cannot establish live runtime evidence.

A devcontainer Dockerfile removes the inherited unused Yarn apt source that caused a missing-signing-key failure. Successful full provisioning from this correction remains unverified.

## Verification

TypeScript/Vite build passed. All 367 UI tests passed across 15 files. The lazy renderer chunk is 606.97 kB, 157.64 kB gzip, with Vite's advisory size warning. Tests cover synchronization, workers, replay, terminal, layout retention, actual instance raycasting, dimensions, motion/camera cancellation and output history.

Restored backend: 450 passed and 4 skipped across 454 collected tests in two non-overlapping groups. Git Bash was placed ahead of an unusable WSL launcher in the test-process PATH. Skips concern POSIX permissions and unavailable GroundTruth query support. Ruff and whitespace checks passed. No benchmark or paid provider dispatch occurred.

Outstanding acceptance: authenticated live observation on the requested Codespace; full application screenshots at 1440x900, 1728x1117, 2560x1440 and narrow widths; comparison with both references; tooltip, keyboard, resizing, reconnect and context-loss review; idle scheduling, repeated-switch GPU resources and integrated-GPU frame times. Browser discovery reports no available browser. Visual fidelity and 60 FPS are not established by these tests.

The handoff document at D:/gt-harness/will_do.docx preserves the dated assessment and separate benchmark repair ledger. No benchmark finding is closed by UI tests. The exact prior assessment referenced by the initial plan remains unidentified. Rollback uses the existing 2D view or baseline UI; city versioned state does not overwrite particle coordinates.
