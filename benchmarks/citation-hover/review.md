# Manual review notes

The following checks compared OCR entries with PDF text geometry; the publisher
screenshots additionally checked the actual visible reader/card. These were used
to correct the benchmark as well as the implementation, rather than accepting
every fuzzy OCR alignment as truth.

| Paper / reference | Observation |
|---|---|
| Teaching Models…, Greenblatt 2024 | Numbered single-column entry already complete in baseline; retained. |
| Towards a Unified…, Chughtai 2023 | Baseline returned “Published as a conference paper at ICLR 2025”; final follows the actual reference. |
| Multi-Agent Risks…, Bengio 2024 and Chan 2023 | Baseline selected preceding entries; destination boundaries select the correct author-year paragraphs. |
| Position: Anthropomorphic…, Park 2024 | Baseline interleaved both columns. Right-column anchors are left of the page midpoint; final separates glyphs before grouping baselines. |
| Manifold Steering…, Pearce 2025 | Baseline returned a different author paragraph; final gets the intended Goodfire reference. |
| Latent Adversarial Training…, Shah 2023 / Zou 2023 | Baseline returned the publication header instead of references. |
| CanSig, bibliography 1 | Next destination lies on the previous entry's last baseline. Final retains the continuation line; verified card screenshot and page-16 jump. |
| Protein Chemical Aging, bibliography 1 / 5 / 8 | Destination x=0 loses column identity. Align by the publisher's reference number, not nearest text. Verified card and page-11 jump. |
| DSAVE, reference 1 | Stored destinations are offset from the visible entries. Coordinate alignment had accidentally labelled a running title; number alignment corrects the label and extraction. Verified page-22 jump. |
| DNA-Diffusion / synthetic regulatory elements, ENCODE reference 1 | Destination uses legacy `%uFEFF` escapes. Verified decoded link, visible complete entry, page-14 jump. |
| When Chain of Thought…, Arcuschin 2025 | Cross-page extraction had included the paper's running title; could falsely match the current paper. Repeated-margin filtering removes it. |
| On scalable oversight…, Bowman 2022 | Same cross-page running-title failure; removed before resolution. |
| Reconstruction…, bibliography 37 | This is a GitHub code deposit quoting a publication title, not the publication itself. Labelled as a negative; keep raw text. |

Title-label review rejected venue-only parses such as “Science (New York, N.Y.)”,
“Commun. on Pure Appl”, “Mol Metab” and “Nucleic Acids Research”. The OCR also
misreads “monosemanticity” as “monoseismicity” in duplicate Activation Oracles
PDFs; those title labels were excluded, not corrected from search results.

Remaining errors mix genuine extraction failures with imperfect OCR: corrupted
reference lists, missing text, paragraph boundaries that OCR merges/splits, and
unusual entries with no next anchor. The aggregate does not claim that every
unlabelled or failing entry is an implementation defect. Four PDFs have no
citation annotations; text-only/superscript hover detection is intentionally
deferred because identifying these without false positives needs its own labelled
geometry benchmark. The 48-record deployment includes duplicate papers, retained
as separate papers as requested.
# Review follow-up spot checks (2026-09-24)

Compared all four changed, now-correct destinations with their stored Mistral
OCR entries in paper `10a6109e-3b90-4192-95d0-500a2ef887bb`:

- `Basu2024APIBLENDAC`: restored “A Comprehensive Corpora for Training and Benchmarking API LLMs”.
- `Everitt2021`: restored “A Causal Perspective” and publication details.
- `Dahia2024`: restored “A Machine Learning Approach” and publication details.
- `sangwan_cybersecurity_2023`: restored “A Survey” and journal details.

All were cut at the title continuation by the old capital-letter heading
pattern. One trial fix misclassified an author-initial line following a tiny
superscript in `cite.kim2026meta`; the final heading rule and an additional
regression case preserve that entry. No previously correct destination was lost.

The 18 synthetic extraction cases reproduce suffix/number collisions, body
numbering, footer continuation, ordinary capitalized text, FitH/FitBH boundaries,
and missing/ambiguous x coordinates, plus positive controls. Before: 6/18;
after: 18/18. FitH/FitBH do not occur in the deployed corpus.

The browser cases reproduced raw destination-key fallbacks, two lookups on
empty/throwing extraction, and a disappearing card after clicking the Add icon.
After the fixes, unavailable references trigger no lookups; the real Add button
keeps the card open through a fully intercepted import. The original SVG target
is confirmed detached, and outside-click dismissal still works. Before: 1/5;
after: 5/5. See [review-results.json](review-results.json) for case-level evidence.
