"""Resolve bibliography entries (the reader's citation hover cards).

`resolver.resolve_reference(text)` turns one reference-list entry into a
`ResolvedReference`: a paper (Crossref / OpenAlex / arXiv), a web page (its
`citation_*` / OpenGraph / JSON-LD metadata), or "unresolved". `service`
adds the owner's library on top and caches results in
`reference_resolutions`; `api` exposes the batch endpoint.
"""
