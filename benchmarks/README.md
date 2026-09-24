# Benchmarks

Run these commands from the repository root. Each benchmark keeps fixtures and generated output in its git-ignored `.data/` directory.

- [Citation hover](citation-hover/README.md): extraction, resolution, latency and pointer interactions against the deployed PDFs. Run: `node benchmarks/citation-hover/run.mjs`.
- [Annotation jump](annotation-jump/README.md): anchor correctness and click-to-scroll success/latency against the deployed PDFs and highlights. Run: `node benchmarks/annotation-jump/run.mjs`.

Add other benchmarks in their own directories and list their run commands here.
