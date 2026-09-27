// Copies the runtime assets pdf.js loads over HTTP out of node_modules and into
// public/, so they always match the installed pdfjs-dist rather than drifting
// from a vendored copy. Kept in sync by `prebuild` and `predev`.
//
// Everything lands under a directory named after the installed pdfjs-dist
// version, and the viewer builds its URLs from `pdfjsLib.version` (see
// `src/components/reader/pdfjs.ts`). Bumping pdfjs-dist therefore changes every
// asset URL, so no browser / proxy cache can keep serving a worker from the
// previous release ("The API version X does not match the Worker version Y").
//
// - build/pdf.worker.min.mjs -> public/pdfjs/<version>/pdf.worker.polyfilled.mjs
//   (GlobalWorkerOptions.workerSrc), with src/components/reader/pdfjsPolyfills.js
//   prepended: the modern build needs Map.getOrInsertComputed, which Safari 18.x lacks.
//   If the polyfill changes, rename the file (and workerSrc in pdfjs.ts) — the
//   directory is served `immutable`, so browsers never re-fetch a known URL.
// - cmaps/          -> public/pdfjs/<version>/cmaps/           (cMapUrl)
// - standard_fonts/ -> public/pdfjs/<version>/standard_fonts/  (standardFontDataUrl)
// - wasm/           -> public/pdfjs/<version>/wasm/            (wasmUrl)

import { createRequire } from 'node:module';
import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const clientDir = dirname(dirname(fileURLToPath(import.meta.url)));
const pdfjsDir = dirname(require.resolve('pdfjs-dist/package.json'));
const { version } = require('pdfjs-dist/package.json');

const publicDir = join(clientDir, 'public');
const outDir = join(publicDir, 'pdfjs');
const versionDir = join(outDir, version);

await rm(outDir, { recursive: true, force: true });
// Pre-versioning location of the worker; make sure a stale copy never ships.
await rm(join(publicDir, 'pdf.worker.mjs'), { force: true });
await mkdir(versionDir, { recursive: true });

for (const dir of ['cmaps', 'standard_fonts', 'wasm']) {
    await cp(join(pdfjsDir, dir), join(versionDir, dir), { recursive: true });
}

const polyfills = await readFile(join(clientDir, 'src', 'components', 'reader', 'pdfjsPolyfills.js'), 'utf8');
const worker = await readFile(join(pdfjsDir, 'build', 'pdf.worker.min.mjs'), 'utf8');
await writeFile(join(versionDir, 'pdf.worker.polyfilled.mjs'), `${polyfills}\n${worker}`);

// Handy for checking what a deployment serves: `curl <host>/pdfjs/version.json`.
await writeFile(join(outDir, 'version.json'), JSON.stringify({ version }) + '\n');

console.log(`[pdfjs] synced worker + cmaps/standard_fonts/wasm for pdfjs-dist ${version} -> public/pdfjs/${version}/`);
