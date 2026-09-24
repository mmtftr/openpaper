# Client

Next.js 15 (App Router) front end: the paper reader, chat, notes, library,
projects, discover and Settings. See [DEVELOPMENT.md](../DEVELOPMENT.md) for
the whole stack.

- In compose: the `client` service, built with `output: "standalone"` and run
  as `node server.js` on `127.0.0.1:12000`. `NEXT_PUBLIC_*` values are build
  args and are baked into the bundle.
- On the host: `yarn && yarn dev -p 8002`, with `NEXT_PUBLIC_API_URL` in
  `.env.local` (see `.env.example`).
- API types: `yarn gen:api` regenerates `src/lib/api/openapi.json` and
  `schema.d.ts` from the server; `yarn check:api` checks they agree.
- Checks: `npx tsc --noEmit -p .`, `yarn lint`, `yarn check:api`, `yarn build`.

### pdf.js runtime assets

The reader loads pdf.js's worker, cmaps, standard fonts and wasm over HTTP from
`public/pdfjs/<version>/`. Those files are *generated*, not committed:
`scripts/sync-pdfjs-assets.mjs` copies them out of `node_modules/pdfjs-dist` so
they always match the installed version. It runs automatically via the `predev`
and `prebuild` hooks.

The version segment is deliberate: `src/components/reader/pdfjs.ts` builds the
URLs from `pdfjsLib.version`, so bumping `pdfjs-dist` changes every asset URL and
no browser can keep using a cached worker from the previous release (pdf.js
refuses to run when worker and API versions differ). `next.config.ts` serves the
directory with an immutable cache header for the same reason. What a deployment
serves is visible at `/pdfjs/version.json`.

If you start Next directly (`npx next dev`) rather than through `yarn dev`, the
copy step is skipped and the PDF pane fails with worker//cmap 404s. Run it by
hand in that case:

```bash
node scripts/sync-pdfjs-assets.mjs
```
