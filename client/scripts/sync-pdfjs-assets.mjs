// Copies the runtime assets pdf.js loads over HTTP out of node_modules and into
// public/, so they always match the installed pdfjs-dist rather than drifting
// from a vendored copy. Kept in sync by `prebuild` and `postinstall`.
//
// - build/pdf.worker.min.mjs -> public/pdf.worker.mjs  (GlobalWorkerOptions.workerSrc)
// - cmaps/          -> public/pdfjs/cmaps/           (cMapUrl)
// - standard_fonts/ -> public/pdfjs/standard_fonts/  (standardFontDataUrl)
// - wasm/           -> public/pdfjs/wasm/            (wasmUrl)

import { createRequire } from 'node:module';
import { cp, mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const clientDir = dirname(dirname(fileURLToPath(import.meta.url)));
const pdfjsDir = dirname(require.resolve('pdfjs-dist/package.json'));
const { version } = require('pdfjs-dist/package.json');

const publicDir = join(clientDir, 'public');
const outDir = join(publicDir, 'pdfjs');

await rm(outDir, { recursive: true, force: true });
await mkdir(outDir, { recursive: true });

for (const dir of ['cmaps', 'standard_fonts', 'wasm']) {
    await cp(join(pdfjsDir, dir), join(outDir, dir), { recursive: true });
}

await cp(
    join(pdfjsDir, 'build', 'pdf.worker.min.mjs'),
    join(publicDir, 'pdf.worker.mjs')
);

// The viewer reads this at runtime to assert the served assets match the
// bundled library; a mismatch means the sync step did not run.
await writeFile(join(outDir, 'version.json'), JSON.stringify({ version }) + '\n');

console.log(`[pdfjs] synced worker + cmaps/standard_fonts/wasm for pdfjs-dist ${version}`);
