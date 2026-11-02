# Single-page dashboard, weekly timetable and story graph — 2026-11-02

This revision supersedes the sidebar/multiple-view layout described in
`dashboard-redesign-2026-11-02.md`.

## Implemented

- Removed sidebar navigation. Present state, weekly timetable, story graph and
  compact disclosure cards now share one vertically scrollable page.
- Seven weekday columns remain present. Records are grouped by two-hour start
  window, without implying that their full duration is two hours. Clicking a
  single loaded item opens its original details; dense windows open an item list.
  Complete start/end times and explicit related entries remain in the drawer.
- Added bounded whole-week slot metadata before detail pagination: counts by
  kind, page bounds and up to two already-authorized published course titles per
  slot. It adds no immutable content reads. Unknown/unloaded and empty cells are
  separate states. Each selected detail page still comes from one snapshot.
- Added snapshot-local opportunity indices, exact visible predecessor indices and
  thread indices to the story projection. These exclude withheld opportunities
  and expose no domain identifiers. Optional fields preserve legacy JSON output
  when absent.
- Story maps render SVG connectors with interactive HTML nodes, zoom and bounded
  scrolling. Solid arrows mean recorded prerequisites, including forks and
  multiple prerequisites converging on a node. Dotted edges mean membership.
  Summary order is never converted into a causal path; unknown game-like choices
  or endings are not invented. Legacy snapshots without links remain readable.
- Details use a native modal drawer (desktop side panel, mobile full width).
  Escape, close, focus restoration and the original snapshot time are retained.
  Graph pan, zoom and disclosures persist across polling; recording focus is
  reversible without permanently opening all cards.

## Verification

76 Python checks passed across agenda, UI, browser wrapper, home snapshots,
public projections and owner HTTP adapter tests. The browser wrapper runs
58 JavaScript checks. There is one pre-existing Starlette/httpx deprecation
warning. Scoped diff whitespace checks and Python compilation passed.

Regression checks cover whole-week metadata with bounded original reads,
legacy serialization, withheld predecessor exclusion, explicit branch/merge
edges, duplicate thread titles, cycle termination, date/page requests, exact
clicked records, stable graph scrolling and reversible recording disclosures.

Browser QA covered 320, 390, 768 and 1440 px layouts. All seven day headings
remain present and the page itself does not overflow horizontally. Wide tables
and graphs scroll inside bounded containers. Verified the absence of the
sidebar, collapsed cards, course and node drawers, Escape, disclosure toggles,
zoom and prerequisite arrows. No captured browser console errors/warnings.

## Evidence and limits

The existing owner on port 8787 still timed out during this session. The
production process was not restarted. Two separate loopback-only QA previews
were used and stopped after verification:

1. The existing authorized 2026-11-01 snapshot, explicitly marked historical.
2. A separately labelled synthetic layout fixture containing seven days and a
   fork/merge graph, used to exercise new projection fields. This fixture was
   never inserted into any World, model context or production source.

No provider calls or World/database writes were made. Live production pagination
and the new owner projection after a process reload remain unverified.

Private local artifacts, excluded from Git:

- `output/dashboard-singlepage-20261102/single-page-sample-desktop.jpg`
- `output/dashboard-singlepage-20261102/story-branches-sample.jpg`
- `output/dashboard-singlepage-20261102/single-page-sample-mobile.jpg`
- `output/dashboard-singlepage-20261102/detail-mobile.jpg`
- `output/dashboard-singlepage-20261102/historical-snapshot-desktop.jpg`
- `output/dashboard-singlepage-20261102/layout-checks.json`
