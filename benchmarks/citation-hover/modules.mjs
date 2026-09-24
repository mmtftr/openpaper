import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

export const bench = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(bench, '../..');
export const source = path.join(root, 'client/src/components/reader/citations');
export const out = path.join(bench, '.data');
export const clientRequire = createRequire(path.join(root, 'client/package.json'));
const ts = clientRequire('typescript');

export async function modules(variant = 'current') {
  const directory = path.join(out, 'modules', variant);
  await fs.mkdir(directory, { recursive: true });
  for (const name of ['helpers', 'bibliography', 'resolve', 'api']) {
    const variantSource = variant === 'current' ? source : path.join(out, variant + '-source');
    const input = name === 'api' ? path.join(root, 'client/src/lib/api.ts') : path.join(variantSource, name + '.ts');
    let code = ts.transpileModule(await fs.readFile(input, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
    code = code.replaceAll('"@/lib/api"', '"./api.mjs"').replaceAll('"./helpers"', '"./helpers.mjs"');
    await fs.writeFile(path.join(directory, name + '.mjs'), code);
  }
  return {
    ...await import(path.join(directory, 'helpers.mjs')),
    ...await import(path.join(directory, 'bibliography.mjs')),
    ...await import(path.join(directory, 'resolve.mjs')),
  };
}
