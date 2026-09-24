# Citation benchmark

From the repository root, on this Docker host:

```sh
node benchmarks/citation-hover/run.mjs
```

This uses the existing Playwright installation and Chromium 149 cached on this
host. The default Playwright Chromium 143 lacks a method required by this pdf.js
build. No package installs are needed. The runner starts a localhost-only Next
server on 3107 if one is not already running, with `CITATION_BENCHMARK=1` and
`NEXT_DIST_DIR=../benchmarks/citation-hover/.data/next` (relative to `client/`). It stops only a server it started.
Scripts resolve packages from the existing `client/node_modules`; the runner
also creates `.data/node_modules` as a link to it for Next's generated server
files and types. No separate benchmark dependency installation is needed.

The harness mounts the real `PdfReader`, including its normal `PdfPane`, pdf.js
annotation layers, citation hook, Jotai store and preview card. A separate dev
route avoids production authentication and changing reader interfaces. Both the
route and fixture endpoint return 404 in production or without the explicit env
flag. Search responses are intercepted by Playwright. Jump buttons navigate the PDF.
The import regression check intercepts both the PDF download and upload requests
in Playwright; it never sends an upload to the deployment.
The Next harness stays in `client/src/app/(benchmark)/citation-benchmark/`;
benchmark scripts and reports live here, outside the app source.

Fixtures, the Next build cache and detailed outputs live in **`benchmarks/citation-hover/.data/`**, which is
git-ignored. This includes all 48 PDFs (183 MiB), OCR, text geometry, original
source snapshots, response recordings, screenshots, per-link measurements,
ground-truth alignments and baseline browser traces. Never commit that directory.
`export.py` only issues a database SELECT and GETs PDFs through MinIO using the
same `s3_object_key`/bucket mapping as the server. No containers are restarted.

`summary.json` and `summary.md` are the aggregate output. Each extraction JSON
contains `perPaper`, `rows` (unique destinations), and `perLink` (every recognized
reference annotation, with text/label/accuracy). The baseline source is pinned to
`8187e1cae736c745d775d4cd5519690932f8b474`, with original working-tree files saved
before edits. Extraction and resolution rerun both versions. Browser baseline
traces were recorded **before** changing the implementation and are retained in
the ignored fixture directory; the runner reruns current browser trials. A fresh
checkout without these host fixtures cannot reproduce the historic browser trace
or query recordings; export alone cannot recreate historical service responses.

## What the numbers mean

* **Coverage:** census all PDF link annotations/destinations. Separate physical
  annotation boxes from OCR citation targets: an author and year may be separate
  links, whereas a range of several references may be one link. OCR regexes
  expand numeric ranges and identify author/year and superscript mentions before
  the reference section. These counts are estimates, not an exact universal
  text-citation detector. Four annotation-free papers have 156 detected targets;
  they remain unsupported. Do not combine the two units into one percentage.
* **Extraction:** reference lists are parsed independently from Mistral OCR.
  Ground-truth alignment uses destination-column opening text, or explicit
  publisher reference numbers, **never the implementation's extracted entry**.
  Ambiguous alignments (score < .62 or margin < .1) remain unlabelled. Numbered
  labels must agree with the destination's number. Compare normalized character
  4-gram sets: precision ≥ .90 and recall ≥ .85 passes. Whitespace, accents,
  punctuation and line-wrap hyphens are normalized. Both unique-destination and
  link-weighted results are reported. One deployed paper has JSON-null OCR.
* **Resolution:** 94 reviewed labels across reference styles, including one
  software reference that quotes a paper title and must not resolve to that
  paper. Sampling takes at most two library-title matches and two other parsed
  titles per paper, in first-citation order, before manual review. Title/DOI come from OCR; known library titles and quoted titles are
  aligned literally. Venue sentences rejected by manual review are saved
  separately. No LLM judge was needed: ambiguous OCR alignments are withheld.
  See `review.md` for spot checks and limitations.
* **Fixed response replay:** 487 actual backend responses were saved. Successful
  case/punctuation/whitespace-equivalent queries reuse the same response. Missing
  recordings return unavailable, never invented search results. Precision counts
  every displayed paper; recall uses references confirmed in the library or a
  successful gold-title OpenAlex response. It is a **lower bound** because new
  queries may lack recordings. OpenAlex returned 429s (wrapped as backend 500s)
  during recording; their frequency is reported, and later live recording is
  serialized with a circuit breaker on 429. This is not an exhaustive OpenAlex
  recall claim.
* **Browser latency:** six real reader/PDF cases, one cold and four warm hovers
  per case; fixed 150 ms empty search responses isolate UI behavior. Measure from
  pointer action to non-skeleton card text. Include Playwright action overhead.
  Separately report actual live request latency and a live authenticated library
  hover (one cold + seven warm trials). Add `--live-library` to refresh that live
  sample; otherwise retain the recording. Session tokens stay in memory.

`browser.mjs` tests hover, entering/leaving the card, scroll, click position and
click preview. `transitions.mjs` tests adjacent links, quick pointer passes and
Escape. `publisher-browser.mjs` exercises the six newly supported publisher
formats, compares displayed reference text and clicks the actual jump button.
`contracts.mjs` checks identifier extraction, Unicode/integer/missing destinations,
contradictory authors, software citations and abort behavior.
`review-contracts.mjs` adds 18 extraction cases covering citation-key suffixes,
numbered body text, footer/page continuation, heading false positives,
superscripts, FitH/FitBH boundaries and missing horizontal coordinates.
The PDF inventory preserves the original destination kind and arguments.
`review-browser.mjs` checks missing/empty/throwing extraction without network
lookup, clicking an icon detached by React during import, and outside dismissal.
Its extraction-failure cases mount the real `PdfPane`, citation hook and card
with a fault injected into the document's text API inside the gated harness.
The import check uses the full `PdfReader` and its real Add button.

The full runner executes these checks and rejects any previously correct
destination lost against the preserved pre-review measurement (when present).
`review-summary.json` and `review-summary.md` in `.data/` compare the review
snapshot with the current run. The pre-review sources and measurements are
preserved in `.data/review-before-source/` and `.data/review-before/`.
The synthetic extraction checks can also run alone:

```sh
node benchmarks/citation-hover/review-contracts.mjs
```

## Validation

```sh
cd client
npx tsc --noEmit -p .
node --import ../benchmarks/citation-hover/lint.mjs node_modules/eslint/bin/eslint.js src/components/reader/citations src/components/reader/atoms.ts 'src/app/(benchmark)/citation-benchmark' 'src/app/(benchmark)/layout.tsx' next.config.ts
cd ..
node --import ./benchmarks/citation-hover/lint.mjs client/node_modules/eslint/bin/eslint.js --config client/eslint.config.mjs benchmarks/citation-hover/*.mjs
```

The ordinary ESLint invocation currently fails before linting because the repo
overrides minimatch to v9 while `@eslint/eslintrc` imports the old default
callable. `lint.mjs` adapts that export in the lint process only. It writes its
small shim inside the ignored benchmark directory; dependencies stay unchanged.
