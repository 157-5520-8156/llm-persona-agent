# Dashboard information hierarchy and responsive redesign — 2026-11-02

## Scope

Presentation-only changes to the existing authenticated dashboard. Retains the
owner DTO, privacy filtering, status semantics, read-only endpoints, recording
mode and display pause. No World authoring, provider calls, ledger writes or QQ
process restart were performed. Existing unrelated worktree changes were kept.

- Overview: one primary activity reading with an SVG clock derived from Logical
  Time, a recent-life timeline, compact relationship/emotion cards, and a short
  empty-matters section. Expired expectations remain accessible in history.
- Agenda: chronological periods, nested publication phases and plan-related
  entries using explicit DTO relationships. Time overlap alone never creates
  parentage. Long background windows are disclosed separately from daily events.
- Story: current chapter summary, selected story, progress, unresolved question,
  and related candidates. Mobile uses a native story selector; other chapters
  start collapsed.
- Costs: three summary values, an interactive SVG daily chart and category donut,
  category → function disclosures, model details, and collapsed recent records.
  Inactive functions are folded. Selection survives polling; mobile has a date
  selector. Subscription equivalence is labelled separately from payments.
- Text: known NPC statement envelopes display the exact utterance with its report
  boundary; unknown structured content remains in an expandable original record.
  Long prose expands without insertion through HTML.
- Styling: one presentation stylesheet, teal palette, SVG navigation icons,
  restrained clock/mechanism motion and reduced-motion support. Photos use an
  adaptive grid with their existing preview behavior.

## Validation

- `pytest -q tests/world_v2/test_world_v2_dashboard_ui.py tests/world_v2/test_dashboard_home_browser_runtime.py`: **21 passed**. One existing Starlette/httpx deprecation warning.
- The exported dashboard script plus `tests/js/dashboard_home_client.test.js` and
  `tests/js/pixel_home_bridge.test.js`: **54 passed**. Includes regression checks
  for statement attribution, terminal expectations, explicit relation grouping,
  nested periods, cost selection persistence, and inactive cost modules.
- Python compilation, JavaScript parsing and scoped `git diff --check` passed.
- Browser QA: all six sections at **320, 390, 768 and 1440 px** had no page-level
  horizontal overflow. Desktop/tablet card intersections were checked; none
  occurred. Final desktop overview was rechecked after compact column placement.
  Mechanism diagrams retain a bounded horizontal scroller on small screens.
- Browser exercised cost date selection, function disclosure, section navigation
  and the mobile story selector layout; no captured console errors/warnings.

## Evidence and limits

The live owner snapshot endpoint on port 8787 timed out during this session. The
local QA page therefore explicitly displayed a non-live historical-data banner
and used the already-authorized snapshot saved on 2026-11-01 (81 agenda entries
on the captured page). Cost layout used a read-only snapshot of the configured
production ledger file `data/companion.epoch2.sqlite` through the existing cost
reader. This validates presentation, not current owner availability or live
pagination. The production process on 8787 was not restarted and is not claimed
to be serving this revision.

Private local QA artifacts (not committed):

- `output/dashboard-redesign-20261102/overview-desktop.jpg`
- `output/dashboard-redesign-20261102/overview-mobile.jpg`
- `output/dashboard-redesign-20261102/cost-desktop.jpg`
- `output/dashboard-redesign-20261102/story-desktop.jpg`
- `output/dashboard-redesign-20261102/layout-checks.json`

The temporary historical-preview server was stopped after QA. No snapshot or
credential was embedded in the shipped dashboard source.
