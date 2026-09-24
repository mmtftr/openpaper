# Annotation jump benchmark

From the repository root:

```sh
node benchmarks/annotation-jump/run.mjs
```

The command exports the Docker deployment's PDFs, highlights, annotations and
independent PyMuPDF word boxes on its first run. Subsequent runs use the same
snapshot. Use `--refresh-fixtures` to deliberately take a new snapshot,
`--label=experiment` to keep another report, or `--limit=2` for a smoke test.
Full per-highlight results, PDF SHA-256 hashes and the Next log go into
`benchmarks/annotation-jump/.data/`. That entire directory is
git-ignored. Do not add PDFs or private annotation content to version control.

The exporter only issues SELECTs to Postgres and GETs to local MinIO. It uses
PyMuPDF already installed in the jobs worker through stdin; it does not write
container files, modify the DB, or restart containers. The runner starts its
own Next dev server on an OS-assigned loopback port. Each run copies the client
sources into a temporary `.data/app-*` directory, so concurrent edits and hot reload
cannot affect measurements or rewrite the working tree’s TypeScript settings.
`NEXT_DIST_DIR=.next` is relative to that snapshot, so all Next build output also
stays under `.data/`. It closes the server and removes this snapshot afterward.
The older standalone Next build output is retained in `.data/next/`.
Scripts resolve Playwright from the existing `client/node_modules` using
`createRequire(client/package.json)`, like the citation benchmark. Playwright
remains pinned in the client's devDependencies; its matching Chromium must
already be cached. The runner does not install packages or browsers.

For a quick check from the repository root, preserving the full reports:

```sh
node benchmarks/annotation-jump/run.mjs --limit=1 --label=smoke
```

## What is measured

The development harness mounts the actual `PdfReader` and `AnnotationsView`.
Playwright clicks the actual annotation rows. It does not invoke a scroll API
or set reader atoms to implement a jump. Scrolling away and pressing the real
zoom buttons are trial setup. This avoids production authentication and removes
network/API variance unrelated to reader navigation. Both the route and fixture
endpoint return 404 in production and require `ANNOTATION_BENCHMARK=1` in dev.
The Next harness stays in `client/src/app/(benchmark)/reader-benchmark/`;
benchmark scripts and reports live here, outside the app source.

Every highlight gets a jump from a distant starting scroll position. Each paper
also gets a cold click (preferring a text-only highlight), a repeated click after
scrolling away, and clicks at two zoom levels. Cold starts include reader and
PDF loading time. The report records whether the anchor existed at click time
and whether the target page had been rendered.

Success requires the first drawn rect to fit inside the PDF scroll viewport,
with 8 px of vertical clearance, and the page's text layer to be rendered. The
scroll and rect must then stay unchanged for 100 ms. Time starts in the panel
click handler, not at the beginning of Playwright's actionability wait. Each
trial has a 1500 ms deadline. Latency percentiles describe successful trials;
failures are counted separately, never silently removed from success rates.

After the click trials, a separate PDF document calls the reader's actual anchor
functions. This cannot warm the reader's caches before a cold click. Each
result includes all rects, independently extracted PyMuPDF text under those
rects, normalized character-bigram precision/recall, and agreement between
stored and text-resolved anchors where both exist. The conservative automatic
correctness gate is recall >= 0.85 and precision >= 0.70. It penalizes unrelated
text covered by overly broad geometry. It is a spatial/text check, not a human
annotation ground truth: LaTeX markup and extraction order can produce false
negatives. Missing anchors and low scores remain in the JSON for review.

The baseline was measured before implementing geometry jumps or changing quote
resolution. `--legacy` can additionally exercise the old panel action on the
current reader; that is an interaction comparison, not a reconstruction of the
old anchoring algorithm.

## Results

Low PyMuPDF scores also get a second check using actual browser DOM word boxes;
these diagnostics do not replace or relax the independent primary scores.

See `results.json` for the saved aggregate comparison and this file’s measured
results below. Full reports remain in the ignored fixture directory.

## Review notes

- The implementation uses stored geometry first. Text-only requests preempt the
  background queue, share in-flight page text extraction, and try `page_number`
  first. Repeated clicks create new requests. A manual scroll, new navigation,
  document change, or newer request cancels pending navigation/corrections.
- Scrolling is immediate in both axes, with the first rect roughly one third
  down the viewport. A bounded correction loop covers late page dimensions and
  zoom/fit changes. An unresolved quote falls back to the page hint. The mobile
  paper page opens the reader when a thread is clicked.
- The primary benchmark also waits for a rendered text layer, a stricter check
  than just the requested rect visibility. Separate `geometryLatencyMs` values
  make a slow PDF paint distinguishable from an incorrect scroll.
- `--legacy` uses the original panel click behavior with current anchoring.
  Locally, `--legacy --baseline-source=benchmarks/annotation-jump/.data/baseline-source`
  restores the saved pre-change reader files **only inside the temporary source
  snapshot**, for an exact baseline rerun. The path is relative to the repository
  root. That saved source is local, not a
  required fixture for the normal benchmark command.
- No changes were made to `AnnotationsView`, `HighlightPopover`,
  `InlineAnnotationCard`, `captureAnchor`, `anchorFromScaledPosition`, or highlight
  rect merging. Their concurrent changes remain intact.

## Navigation review follow-up

`navigation.mjs` exercises pending text-only lookups while using the actual
page input, thumbnail sidebar, native outline, page-percentage outline, toolbar
zoom, wheel, PageDown, and citation controls. The harness holds the target
page's real `getTextContent()` promise until after the later navigation settles,
then releases it and observes for 1600 ms, including the lookup fallback deadline.
The percentage-outline case replays a controlled outline destination through
`Outline`; it never calls the backend's outline generation/cache endpoint.
No anchor or jump result is stubbed.

It also remounts the entire reader (including its Jotai provider), refreshes the
PDF URL to create a new document proxy, remounts during an unresolved lookup,
and verifies that a fresh click on the same thread works afterward. The
immutable request remains in its parent state throughout reconstruction, just
as it does across the paper page's desktop/mobile layouts.

The viewer now owns one cancellation token spanning lookup and scrolling.
Page/percentage/find/zoom navigation and reader input (including toolbar and
sidebar capture events) invalidate it. Navigation history's Back action also
invalidates it. Request consumption is stored outside the reader lifecycle,
keyed by the caller-owned request plus document fingerprint, highlight and nonce;
weak keys release that record when the caller drops the request. A StrictMode
setup/cleanup probe does not consume a request before it starts.

Run the review matrix from the repository root:

```sh
node benchmarks/annotation-jump/run.mjs --label=review-final
```

Add `--limit=1 --label=review-smoke` for the smoke matrix and navigation checks.
Any failed resilience/navigation check makes the runner exit nonzero after
saving its JSON report. Historical baseline and original final reports remain
unchanged in `.data/`.

The pre-fix run reproduced seven failures among 12 checks. All 12 shared
checks now pass, plus the added percentage-outline check: **13/13**. All six
existing resilience checks also pass. The full rerun covers 221 highlights and
397 clicks; source hashes match the final working reader files. No browser
exceptions or anchor-correctness regressions occurred.

| Review metric | Before fix | After fix |
| --- | ---: | ---: |
| Pending toolbar/page/sidebar navigation (four reproduced races) | 0/4 | 4/4 |
| Completed/pending request replay on remount or URL refresh | 0/3 | 3/3 |
| All shared navigation checks, including positive controls | 5/12 | 12/12 |
| Added page-percentage outline check | — | 1/1 |
| Full render-inclusive success on locatable trials | 348/349 | 348/349 |
| Full successful jump p50 / p95 | 146 / 609 ms | 147 / 599 ms |
| Full geometry-only success | 349/349 | 349/349 |
| Full geometry-only p50 / p95 | 139 / 498 ms | 143 / 511 ms |
| Located / primary-correct / unlocated highlights | 203 / 197 / 18 | 203 / 197 / 18 |

The remaining render timeout is on the same 31 MiB PDF as before: the geometry
was visible and settled after 204 ms, but text painting exceeded 1500 ms. The
existing unlocatable quotes and independent-oracle limitations below are
unchanged. TypeScript passes; lint of all touched source and benchmark scripts
has no errors (three existing unused-suppression warnings in reader files).

This follow-up changes `useHighlightJump.ts`, `useViewer.ts`, `jumpToAnchor.ts`,
`useReaderNavigation.ts` and the reader root attribute in `PdfReader.tsx`.
Benchmark changes are `navigation.mjs`, `run.mjs`, the dev harness controls,
this write-up and the `navigationReview` comparison in `results.json`.

## Measured comparison (2026-09-24)

Chromium 149.0.7827.55, 1440 × 1000, real fixtures from 48 papers (44 have
highlights), 221 highlights, 397 click trials per matrix. Both primary matrices
were repeated using isolated source snapshots. No primary anchor correctness
regressions occurred. Stored/text anchors agreed on the page in all 98 comparable
cases (median first-rect distance 0.293% of page dimensions).

| Metric | Baseline | Final |
| --- | ---: | ---: |
| Located highlights | 187/221 | 203/221 |
| Independent spatial/text gate passes | 181/221 | 197/221 |
| Unlocated highlights | 34 | 18 |
| Render-inclusive success on locatable trials | 74/321 (23.1%) | 348/349 (99.7%) |
| Successful jump p50 / p95 | 397 / 807 ms | 146 / 609 ms |
| Repeat success, locatable | 0/34 | 37/37 |
| Zoom-in + zoom-out success, locatable | 0/68 | 74/74 |

The final **geometry-only** result is **349/349**, p50 **139 ms**, p95 **498 ms**.
All six supplementary checks passed: rapid replacement, zoom during a jump,
resize during a jump, manual-scroll cancellation, citation find after a jump,
and page fallback for a genuinely unlocated fixture.

| Scenario | Baseline successes / trials | Final successes / trials | Final p50 / p95 |
| --- | ---: | ---: | ---: |
| Cold, including PDF loading | 2/44 | 35/44 | 596 / 1023 ms |
| Distant start, every highlight | 72/221 | 202/221 | 155 / 260 ms |
| Repeat after leaving the page | 0/44 | 37/44 | 129 / 148 ms |
| Zoom in | 0/44 | 37/44 | 122 / 130 ms |
| Zoom out | 0/44 | 37/44 | 123 / 131 ms |

The first geometry implementation reached 200 anchors, 341/342 render-inclusive
successes, and 147/603 ms p50/p95. Unicode compatibility and escaped TeX delimiter
normalization then recovered three more anchors without regressing existing ones.
The final cold p95 still exceeds 1 s slightly because it includes document loading;
the aggregate and already-loaded jump percentiles are comfortably below 1 s.

### Remaining cases and measurement limits

- **18 unlocated quotes:** some AI quotes alter source wording (for example,
  `BsIGO` versus the PDF's `Bs1GO`); others contain equations or text whose
  extraction order interrupts the quote. These retain the bounded page-hint
  fallback. We did not add broad fuzzy matching that could select unrelated text.
- **One render deadline miss:** a 31 MiB PDF landed at its target in 128 ms, but
  its text layer was not ready at 1500 ms. Geometry navigation succeeded; the
  stricter render-inclusive check failed. Cold PDF rendering remains a separate
  bottleneck, particularly for this document.
- **Six primary correctness flags are oracle limitations:** PyMuPDF omits marked
  body text in one paper. Browser DOM word boxes verified all five affected
  highlights with precision and recall of 1. The remaining flag is a caption
  whose LaTeX command names differ from its correctly covered rendered notation.
  Both baseline and final retain the same six flags; scores were not relaxed.
- The isolated baseline's optional DOM diagnostic initially timed out because it
  used the legacy annotation click to load its target page. All **397 primary
  trials and 221 independent anchor audits had already completed**. This extra
  diagnostic now uses the page toolbar and completed successfully in the final
  run. Both the original complete baseline and isolated baseline raw reports are
  retained locally.
- The auth-free harness tests the real reader/panel path, not login/API latency.
  It does not measure deployment network latency or mobile browser performance.

### Validation

After relocation, `node benchmarks/annotation-jump/run.mjs --limit=1 --label=relocation-smoke`
passed all 9 jumps, all 5 anchor checks, and all 5 applicable resilience checks,
with no browser errors. The report is `.data/relocation-smoke.json`; the original
full baseline/final reports and aggregate comparison above are preserved.
TypeScript and lint of the relocated scripts and fixture route also pass.

`cd client && npx tsc --noEmit -p .` passes. Lint was run on all changed TS/TSX/JS
files with the repository's Next/TypeScript rules. No new errors were introduced.
The paper page has ten pre-existing unused-variable errors. The normal ESLint
loader also has existing minimatch/ESLint-patch incompatibilities; a temporary
local CJS loader shim applied the same project rules without changing dependencies
or the tracked ESLint configuration. There are three existing unused suppression
warnings in reader files.

Changes remain uncommitted. The Docker deployment, DB, and PDF objects were not
modified.

### Files changed for this task

- `client/src/app/(paper)/paper/[id]/page.tsx`: create repeatable jump requests, cancel
  them for citation/document navigation, and open the reader on mobile.
- `client/src/components/reader/PdfReader.tsx` and `index.tsx`: expose and handle the
  request while retaining citation search and active highlight styling.
- `client/src/components/reader/useHighlightJump.ts`, `jumpToAnchor.ts`, and
  `useViewer.ts`: bounded lookup, cancellation, instant geometry scrolling,
  horizontal alignment, and layout corrections.
- `client/src/components/reader/useAnchoredHighlights.ts` and the quote-location parts
  of `anchoring.ts`: prioritized resolution, document-scoped results, shared
  in-flight indexes, and conservative text normalization.
- `client/src/app/(benchmark)/layout.tsx` and
  `client/src/app/(benchmark)/reader-benchmark/{page.tsx,Harness.tsx,assets/[file]/route.ts}`:
  opt-in development route and fixture endpoint using the real components.
- `benchmarks/annotation-jump/` (entry point `run.mjs`):
  export, browser driving, independent scoring, supplemental checks, summary,
  results, and this write-up.
- `client/package.json`, `client/yarn.lock`, and the root `.gitignore`: pinned
  Playwright and ignored fixtures/build artifacts. Run directly from the
  repository root; there is no client package script for benchmarks.
