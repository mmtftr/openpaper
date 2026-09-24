# Citation hover benchmark result

Measured on 2026-09-24 against the 48 deployed PDFs.

| Metric | Baseline | Final |
|---|---:|---:|
| Papers with reference-link previews | 38/48 | 44/48 |
| Supported reference links | 8785/9430 | 9430/9430 |
| Correct bibliography destinations | 463/2993 (15.5%) | 2930/2993 (97.9%) |
| Correct bibliography links | 777/7981 | 7831/7981 |
| Resolution precision (recorded responses) | 7/26 (26.9%) | 41/41 (100.0%) |
| Recall on known positives (lower bound) | 14.6% | 85.4% |
| Wrong paper matches | 19 | 0 |
| Cold hover content p50 / p95 | 783 / 805 ms | 234 / 242 ms |
| Warm hover content p50 / p95 | 773 / 793 ms | 212 / 225 ms |
| Card leave dismisses | 0/6 | 6/6 |
| Click opens preview without jumping | 0/6 | 6/6 |
| Adjacent / quick-pass / Escape checks | 0/3 | 3/3 |

Resolution uses 94 reviewed labels and 48 known positives. Recall is a lower bound under fixed response replay; unrecorded queries stay unavailable. OpenAlex returned 429s during live recording. The browser comparison uses controlled 150 ms responses, six PDFs, six cold and 24 warm hovers per implementation.

All six newly supported publisher formats displayed the expected entry and passed jump/dismiss tests. A separate live library sample showed content at 180 ms cold and about 153 ms warm; the matched paper arrived at 370 ms cold. Only one live request was needed for eight hovers.

Four PDFs without usable reference annotations remain unsupported (156 OCR-detected citation targets, an estimate). Another 449 destinations lack confident OCR labels, including the paper with null OCR. Among the 2,993 labelled destinations, 63 still fail the extraction threshold; OCR corruption and irregular reference boundaries contribute.

Run from the repository root:

```sh
node benchmarks/citation-hover/run.mjs
```

Detailed JSON: `benchmarks/citation-hover/.data/summary.json`, with per-paper summaries. The extraction JSONs include every citation link, and the resolution JSONs include predictions, labels and missing-recording lists. Fixtures, full output and historical browser traces are ignored. See [README.md](README.md) for methodology, reproducibility and validation commands, and [review.md](review.md) for manual spot checks.

Validation: TypeScript passed (`npx tsc --noEmit -p .`); touched-file ESLint passed using the process-only adapter for the existing minimatch override; eight citation identity/destination regression checks passed. No server endpoints changed, no database writes, no container restarts, no installs, no commits. Shared-file edits are limited to citation fields in `atoms.ts`, fixture/build-output ignore rules, `NEXT_DIST_DIR` in `next.config.ts`, and permitting the citation benchmark env flag in the existing benchmark layout.


## Review follow-up

Fresh full runs before and after the seven review fixes, on the same 48 PDFs
and recorded search responses. “Before” here is the pre-review implementation,
not the original Git baseline above.

| Metric | Before review fixes | After |
|---|---:|---:|
| Supported links / papers | 9430 / 44 | 9430 / 44 |
| Correct destinations | 2926/2993 | 2930/2993 |
| Correct links | 7826/7981 | 7831/7981 |
| Correct / wrong paper matches | 41 / 0 | 41 / 0 |
| Resolution recall (recorded positives) | 85.4% | 85.4% |
| Cold hover p50 / p95 | 226 / 232 ms | 234 / 242 ms |
| Warm hover p50 / p95 | 212 / 217 ms | 212 / 225 ms |
| Review extraction checks | 6/18 | 18/18 |
| Review browser checks | 1/5 | 5/5 |

All seven findings have regression coverage: 18 extraction cases and five real
browser checks. The import check clicks the SVG that React detaches, holds the
intercepted upload open, and verifies the card both during and after import.
Missing, empty and throwing extraction now display an unavailable state and
make zero lookup requests (before: 0, 2 and 2 requests respectively).

Four previously truncated entries are now correct, accounting for five citation
links; no previously correct destination was lost. All six ordinary browser
cases, all six publisher cases and all three transition checks still pass.
Cold p95 increased by 10 ms and warm p95 by 8 ms in these small samples; no
latency improvement is claimed. Extraction-only p95 was 0.086 ms before and
0.088 ms after. The live latency sample is retained, not newly measured.

Machine-readable review comparison: [review-results.json](review-results.json).
Full per-link output and captured pre-review sources remain under `.data/`.
The corpus has no FitH/FitBH destinations, so synthetic cases cover those kinds;
the inventory now preserves actual destination kinds instead of coercing XYZ.
Four annotation-free PDFs remain unsupported, 449 destinations lack confident
OCR labels, and 63 labelled destinations still fail extraction accuracy.
TypeScript and touched-file ESLint passed. No installs, commits, deployment
changes or database writes.
