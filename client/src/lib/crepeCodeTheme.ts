import { syntaxHighlighting } from '@codemirror/language';
import { classHighlighter } from '@lezer/highlight';
import type { Crepe, CrepeConfig } from '@milkdown/crepe';

type CodeMirrorConfig = NonNullable<NonNullable<CrepeConfig['featureConfigs']>[typeof Crepe.Feature.CodeMirror]>;

/**
 * Code blocks in the Crepe editors. Crepe defaults to CodeMirror's One Dark
 * whatever the app theme; instead tokens get stable `tok-*` classes that
 * globals.css colours per theme. The theme is `null`, not `[]`: Crepe fills
 * feature configs in with lodash `defaultsDeep`, which merges One Dark's
 * extensions into an empty array. A fresh object per editor, since that
 * merge also writes into it.
 */
export function crepeCodeMirrorConfig(): CodeMirrorConfig {
    return {
        theme: null as unknown as CodeMirrorConfig['theme'],
        extensions: [syntaxHighlighting(classHighlighter)],
    };
}
