import type { CSSProperties } from "react";
import type { BundledLanguage, Highlighter } from "shiki";

/**
 * Lazy singleton syntax highlighter.
 *
 * Shiki (grammars + the oniguruma wasm) is pulled in through a dynamic
 * `import()` so none of it lands in the initial bundle — the first fenced code
 * block or code-viewer open pays for it, everything before that is free.
 * Grammars load on demand from a small allow-list; anything else renders as
 * plain text.
 *
 * Colors use shiki's dual-theme output (CSS variables, `defaultColor: false`)
 * so light/dark follows the app's `.dark` class with no re-highlighting on
 * theme change. See the `.shiki` rules in globals.css.
 */

export const CODE_THEME_LIGHT = "github-light";
export const CODE_THEME_DARK = "github-dark";

/** Rendered as plain text — shiki's built-in no-op grammar. */
export const PLAIN_TEXT = "text";

/** Grammars we ship. Everything else falls back to plain text. */
const SUPPORTED_LANGUAGES = [
    "python",
    "typescript",
    "javascript",
    "tsx",
    "jsx",
    "json",
    "yaml",
    "toml",
    "markdown",
    "shellscript",
    "c",
    "cpp",
    "rust",
    "go",
] as const;

type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

const SUPPORTED = new Set<string>(SUPPORTED_LANGUAGES);

/** Common fence tags / file extensions → the grammar that handles them. */
const LANGUAGE_ALIASES: Record<string, SupportedLanguage> = {
    py: "python",
    python3: "python",
    pyi: "python",
    ts: "typescript",
    mts: "typescript",
    cts: "typescript",
    js: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    jsx: "jsx",
    tsx: "tsx",
    json5: "json",
    jsonc: "json",
    yml: "yaml",
    md: "markdown",
    mdx: "markdown",
    markdown: "markdown",
    sh: "shellscript",
    bash: "shellscript",
    zsh: "shellscript",
    shell: "shellscript",
    console: "shellscript",
    h: "c",
    cc: "cpp",
    cxx: "cpp",
    hpp: "cpp",
    hh: "cpp",
    "c++": "cpp",
    rs: "rust",
    golang: "go",
};

/** Repo-relative path → grammar id, by extension. */
export function languageFromPath(path: string): string {
    const name = path.split("/").pop() ?? path;
    if (name === "Dockerfile" || name.startsWith("Dockerfile.")) {
        return PLAIN_TEXT;
    }
    if (name === "Makefile" || name === "makefile") return PLAIN_TEXT;
    const dot = name.lastIndexOf(".");
    if (dot <= 0) return PLAIN_TEXT;
    return normalizeLanguage(name.slice(dot + 1));
}

/** Fence info string (`js`, `python title=x`, …) → a grammar we can load. */
export function normalizeLanguage(lang?: string | null): string {
    if (!lang) return PLAIN_TEXT;
    const token = lang.trim().split(/[\s:,]/)[0].toLowerCase();
    if (!token) return PLAIN_TEXT;
    if (SUPPORTED.has(token)) return token;
    return LANGUAGE_ALIASES[token] ?? PLAIN_TEXT;
}

let highlighterPromise: Promise<Highlighter> | null = null;

async function getHighlighter(): Promise<Highlighter> {
    if (!highlighterPromise) {
        highlighterPromise = import("shiki")
            .then((shiki) =>
                shiki.createHighlighter({
                    themes: [CODE_THEME_LIGHT, CODE_THEME_DARK],
                    langs: [],
                })
            )
            .catch((error) => {
                // Let a later call retry instead of caching the failure.
                highlighterPromise = null;
                throw error;
            });
    }
    return highlighterPromise;
}

const loadedLanguages = new Set<string>();

async function ensureLanguage(
    highlighter: Highlighter,
    lang: string
): Promise<string> {
    if (lang === PLAIN_TEXT || !SUPPORTED.has(lang)) return PLAIN_TEXT;
    if (loadedLanguages.has(lang)) return lang;
    try {
        await highlighter.loadLanguage(lang as BundledLanguage);
        loadedLanguages.add(lang);
        return lang;
    } catch (error) {
        console.error(`Failed to load "${lang}" grammar:`, error);
        return PLAIN_TEXT;
    }
}

/** One highlighted token; `darkColor` rides along as a CSS variable. */
export interface CodeToken {
    content: string;
    color?: string;
    darkColor?: string;
    /** Shiki FontStyle bit flags: 1 italic, 2 bold, 4 underline. */
    fontStyle?: number;
}

export type CodeLine = CodeToken[];

/**
 * Highlight to a `<pre class="shiki">…</pre>` string for markdown code blocks.
 * Throws if shiki can't be loaded — callers fall back to a plain `<pre>`.
 */
export async function highlightToHtml(
    code: string,
    lang: string
): Promise<string> {
    const highlighter = await getHighlighter();
    const resolved = await ensureLanguage(highlighter, lang);
    return highlighter.codeToHtml(code, {
        lang: resolved,
        themes: { light: CODE_THEME_LIGHT, dark: CODE_THEME_DARK },
        defaultColor: false,
    });
}

/**
 * Highlight to per-line token arrays for the file viewer, which needs its own
 * DOM per line (line numbers, scroll-to, range highlighting).
 */
export async function highlightToLines(
    code: string,
    lang: string
): Promise<CodeLine[]> {
    const highlighter = await getHighlighter();
    const resolved = await ensureLanguage(highlighter, lang);
    const lines = highlighter.codeToTokensWithThemes(code, {
        // `resolved` is either a grammar we just loaded or shiki's built-in
        // "text"; both are valid, but the union type can't see that.
        lang: resolved as BundledLanguage,
        themes: { light: CODE_THEME_LIGHT, dark: CODE_THEME_DARK },
    });
    return lines.map((line) =>
        line.map((token) => ({
            content: token.content,
            color: token.variants.light?.color,
            darkColor: token.variants.dark?.color,
            fontStyle: token.variants.light?.fontStyle,
        }))
    );
}

/**
 * Inline style for a token. Both palettes ride along as CSS variables — an
 * inline `color` would win over the `.dark` rule and freeze the token in its
 * light color. `.op-code-token` in globals.css picks the right one.
 */
export function tokenStyle(token: CodeToken): CSSProperties {
    const style: Record<string, string> = {};
    if (token.color) style["--shiki-light"] = token.color;
    if (token.darkColor) style["--shiki-dark"] = token.darkColor;
    const fontStyle = token.fontStyle ?? 0;
    if (fontStyle > 0) {
        if (fontStyle & 1) style.fontStyle = "italic";
        if (fontStyle & 2) style.fontWeight = "bold";
        if (fontStyle & 4) style.textDecoration = "underline";
    }
    return style as CSSProperties;
}
