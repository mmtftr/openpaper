# Client

This server manages the frontend for the Open Paper project, which allows users to upload, chat with, annotate, and manage research papers in one place.

First, ensure you've started the backend server. See `/server` for details.

For the full local Docker stack, run this from the repo root:

```bash
docker compose up --build client
```

For a fresh setup, run:

```bash
yarn go
```

Open [http://localhost:9002](http://localhost:9002) with your browser to use the Docker Compose app.

## Development
To run the development server, use:

```bash
yarn dev
```

### pdf.js runtime assets

The reader loads pdf.js's worker, cmaps, standard fonts and wasm over HTTP from
`public/`. Those files are *generated*, not committed: `scripts/sync-pdfjs-assets.mjs`
copies them out of `node_modules/pdfjs-dist` so they always match the installed
version. It runs automatically via the `predev` and `prebuild` hooks.

If you start Next directly (`npx next dev`) rather than through `yarn dev`, the
copy step is skipped and the PDF pane fails with worker//cmap 404s. Run it by
hand in that case:

```bash
node scripts/sync-pdfjs-assets.mjs
```
