"""`ingest.paragraphs.rejoin`: paragraphs a page break cut mid-sentence are
joined at read time (the markdown API), figures in the gap move below.

The excerpts are real markdown served by `GET /api/paper/markdown`
(2026-09-27), shortened: arXiv 2602.08964 (re-ingested: page furniture
already out of the text), and older ingests that still carry page numbers,
running headers and footnotes in the text.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.paper import detail as paper_detail
from app.ingest import content, paragraphs
from app.ingest.content import Page

FIGURE_1 = "![img-0.jpeg](e863f922-b2eb-454d-9579-4ff5f44314a2)"
CAPTION_1 = (
    "Figure 1. Overview of our goal-directedness analysis. We prompt an "
    "LLM-based agent to reason and act over a fully observable grid world."
)
INTRO = "\n\n".join(
    [
        "## 1. Introduction",
        "Attributing goals to agents helps explain and predict their behaviour "
        "and provides a useful abstraction for reasoning about agency. Goal "
        "attribution has been studied across a",
        FIGURE_1,
        CAPTION_1,
        "wide range of fields, including philosophy (Davidson, 1973; Dennett, "
        "1990), economics and decision theory (von Neumann & Morgenstern, 1944; "
        "Savage, 1948),",
        "and reinforcement learning (Bellman, 1966; Ng & Russell, 2000). More "
        "recently, the question of when goal attributions are warranted has "
        "become increasingly important.",
        "A natural way to measure goal-directedness is through behavioural evaluation.",
    ]
)


def test_page_break_around_a_figure_is_joined():
    assert paragraphs.rejoin(INTRO) == "\n\n".join(
        [
            "## 1. Introduction",
            "Attributing goals to agents helps explain and predict their "
            "behaviour and provides a useful abstraction for reasoning about "
            "agency. Goal attribution has been studied across a wide range of "
            "fields, including philosophy (Davidson, 1973; Dennett, 1990), "
            "economics and decision theory (von Neumann & Morgenstern, 1944; "
            "Savage, 1948), and reinforcement learning (Bellman, 1966; Ng & "
            "Russell, 2000). More recently, the question of when goal "
            "attributions are warranted has become increasingly important.",
            FIGURE_1,
            CAPTION_1,
            "A natural way to measure goal-directedness is through behavioural "
            "evaluation.",
        ]
    )


def test_rejoin_is_idempotent_and_a_no_op_returns_the_input():
    once = paragraphs.rejoin(INTRO)
    assert paragraphs.rejoin(once) == once
    unchanged = "# Title\n\nOne sentence.\n\n\n\nAnother."
    assert paragraphs.rejoin(unchanged) is unchanged
    assert paragraphs.rejoin("") == ""


# -- old ingests: page numbers and running headers in the text ---------------------

HEADER = "Position: Anthropomorphic Misalignment Research Needs Stronger Evidence"


def test_page_number_and_running_header_go_and_the_hyphen_is_mended():
    md = "\n\n".join(
        [
            HEADER,
            "Probes for detecting deception may misfire on contextual "
            "correlates of “deception-like” settings, such as high-stakes "
            "vocabulary, role-play framing, or neg-",
            "5",
            HEADER,
            "ative sentiment. Goldowsky-Dill et al. (2025) acknowledge that "
            "their probes can detect deception-related topics.",
            "6",
            HEADER,
            "Next page.",
        ]
    )
    assert paragraphs.rejoin(md) == "\n\n".join(
        [
            HEADER,
            "Probes for detecting deception may misfire on contextual "
            "correlates of “deception-like” settings, such as high-stakes "
            "vocabulary, role-play framing, or negative sentiment. "
            "Goldowsky-Dill et al. (2025) acknowledge that their probes can "
            "detect deception-related topics.",
            # Not in a joined gap: left alone.
            "6",
            HEADER,
            "Next page.",
        ]
    )


def test_long_running_notice_in_the_gap_goes():
    notice = (
        "bioRxiv preprint doi: https://doi.org/10.1101/2024.09.09.612085; this "
        "version posted September 9, 2024. The copyright holder for this "
        "preprint (which was not certified by peer review) is the "
        "author/funder, who has granted bioRxiv a license to display the "
        "preprint in perpetuity. It is made available under a CC-BY-NC-ND 4.0 "
        "International license."
    )
    md = "\n\n".join(
        [
            notice,
            "Cells were counted, and processed for FACS sorting. Cells with "
            "lentiviral library",
            notice,
            "integrants were selected by gating for GFP.",
            notice,
        ]
    )
    assert paragraphs.rejoin(md) == "\n\n".join(
        [
            notice,
            "Cells were counted, and processed for FACS sorting. Cells with "
            "lentiviral library integrants were selected by gating for GFP.",
            notice,
        ]
    )


def test_table_in_the_gap_moves_below():
    table = (
        "|  Model | Family | Params |\n| --- | --- | --- |\n"
        "|  Gemma 3 27B-IT | Gemma | 27B |"
    )
    head = (
        "We also evaluate a contrastive two-stage variant: a contrastive MLP "
        "( $d \\to 512 \\to 128$ ) projects activations into a style-invariant "
        "space, then XGBoost"
    )
    tail = "classifies the 128-dim embedding  $+5$  scalars (133 features)."
    assert paragraphs.rejoin(f"{head}\n\n{table}\n\n{tail}") == (
        f"{head} {tail}\n\n{table}"
    )


def test_hyphen_is_kept_when_the_paper_hyphenates_the_word():
    md = (
        "We compare fine-tuning with steering.\n\n"
        "Across all settings we find that supervised fine-\n\n"
        "tuning degrades general capabilities."
    )
    assert paragraphs.rejoin(md) == (
        "We compare fine-tuning with steering.\n\n"
        "Across all settings we find that supervised fine-tuning degrades "
        "general capabilities."
    )


# -- what is never joined ----------------------------------------------------------


@pytest.mark.parametrize(
    "md",
    [
        # A caption cut off in the gap: the lowercase text is its rest.
        "Several sequences exceeded the in silico performance of the oracles "
        "compared with the best training\n\n"
        "Fig. 5 | STARR-seq validate cell-type-specific activity of "
        "DNA-Diffusion sequences. d, STARR-seq ratio relative to endogenous "
        "training\n\n"
        "sequences, while the  $y$  axis lists the sequence categories.",
        # A heading in between.
        "The model is trained on the counting task and the probes are\n\n"
        "## 3 Results\n\n"
        "we find that the probes recover the count.",
        # Display math in between, and a paragraph that is a formula.
        "The global mean of the penultimate-layer features is given by\n\n$$\n\\mu_G = \\mathrm{Ave}(h)\n$$\n\n"
        "where the average is over all examples of the training set.",
        "$\\bm{\\mu}_{G}\\triangleq\\operatorname*{Ave}\\{h_{i,c}\\},$\n\n"
        "and the train class-means are defined in the same way.",
        # An equation with its number; a sentence introducing a display.
        "The steered activation is $h_i' = h_i + \\alpha v$ (1)\n\n"
        "where $h_i$ is the original activation at position $i$ in layer l.",
        "We normalize the score by dividing by this coefficient, and clip at 1:\n\n"
        "normalized score equals the minimum of the score and one.",
        # A footnote of an old ingest (marker first) is not a first half ...
        "\\( ^{6} \\) Note that since the 'consciousness' theme also involves "
        "persistence, its prevalence may owe in part to viral spread\n\n20\n\n"
        "new payloads and compare them to the original.",
        # ... and a full stop before a note reference ends the sentence.
        "This effect is robust across all the models we evaluated.[^3]\n\n"
        "which is what the ablation in the appendix also shows.",
        # A transcript line and an identifier.
        "Turn 4 — Coral sends the payload, rationalizing the propagation "
        "directives contained inside\n\n"
        "send_message → Quake\nPerfect. I respect the boundary-setting upfront.",
        # Code OCR'd as paragraphs.
        "grad_ptr[j2] = sum2 * inv_fn;\ngrad_ptr[j3] = sum3 * inv_fn;\n}\n\n"
        "for (int i = 0; i < screened_size; ++i) {\n    int j = screened_list[i];",
        # A running title (Title Case, no punctuation) is not prose.
        "Discovery of Shared Transcriptional States\n\n"
        "performed poorly with batch integration across the datasets.",
        # A bare URL is an old ingest's footnote.
        "The code for all experiments is available in our repository at\n\n"
        "https://github.com/goodfire-ai/causalab/tree/manifold_steering",
        # Short lowercase text under a figure may be the figure's own labels.
        "Training curves for the probes on the counting task are shown in\n\n"
        f"{FIGURE_1}\n\nloss",
        # Lists and tables stay as they are.
        "- the first item of a list that ends without punctuation\n\n"
        "and a lowercase paragraph after it.",
        "|  a | b |\n| --- | --- |\n|  1 | 2 |\n\nand a lowercase paragraph after it.",
    ],
)
def test_not_joined(md):
    assert paragraphs.rejoin(md) == md


def test_uppercase_next_paragraph_is_not_a_continuation():
    md = (
        "Despite this limitation, these sequences consistently demonstrated "
        "accessibility on par with DHS regions and were able to induce\n\n"
        "GATA1 to higher in silico levels than the endogenous sequence."
    )
    assert paragraphs.rejoin(md) == md


# -- the markdown API ----------------------------------------------------------------


def test_markdown_payload_joins_across_pages_after_collecting_notes(monkeypatch):
    pages = [
        Page(1, "The effect has been studied across a[^1]\n\n[^1]: A note."),
        Page(2, "wide range of fields."),
    ]
    monkeypatch.setattr(content, "pages", lambda db, pid: pages)
    monkeypatch.setattr(content, "figures", lambda db, pid: [])

    got = paper_detail._paper_markdown_payload(None, SimpleNamespace(id="p"))  # type: ignore[arg-type]

    assert got.markdown == (
        "The effect has been studied across a[^1] wide range of fields.\n\n"
        "[^1]: A note."
    )
