// pdfjs-dist ships its own webpack-bundled ESM (`build/pdf.mjs`,
// `web/pdf_viewer.mjs`) that declares `var __webpack_exports__` at module
// top level. When Next's dev-mode webpack wraps the module, that `var`
// hoists over the wrapper's own `__webpack_exports__` parameter, so the
// generated `__webpack_require__.r(__webpack_exports__)` prologue runs on
// `undefined` and the whole paper page white-screens with
// `TypeError: Object.defineProperty called on non-object`.
//
// Production builds are unaffected (the prod module wrapper doesn't
// collide), so this only bites `next dev`. The fix renames the dead
// variable inside node_modules — it is declared once and never referenced.
// Idempotent; runs from predev/prebuild so fresh installs are re-patched.

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const TARGETS = [
    'node_modules/pdfjs-dist/build/pdf.mjs',
    'node_modules/pdfjs-dist/web/pdf_viewer.mjs',
];
const NEEDLE = 'var __webpack_exports__ = {};';
const REPLACEMENT = 'var __pdfjsUnusedExports__ = {};';

for (const rel of TARGETS) {
    const path = join(root, rel);
    if (!existsSync(path)) {
        console.warn(`[patch-pdfjs] missing ${rel} — skipped`);
        continue;
    }
    const source = readFileSync(path, 'utf8');
    if (source.includes(REPLACEMENT)) {
        continue; // already patched
    }
    if (!source.includes(NEEDLE)) {
        console.warn(
            `[patch-pdfjs] ${rel}: expected declaration not found — pdfjs-dist ` +
            'may have changed; verify the webpack collision is still present'
        );
        continue;
    }
    writeFileSync(path, source.replace(NEEDLE, REPLACEMENT));
    console.log(`[patch-pdfjs] patched ${rel}`);
}
