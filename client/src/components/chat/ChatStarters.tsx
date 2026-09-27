"use client";

import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";

const COMPREHENSIVE_OVERVIEW_DISPLAY = "Create a comprehensive overview";
const COMPREHENSIVE_OVERVIEW_PROMPT =
    "Create a comprehensive, thoughtful brief for this paper. Separate each section with clear headings covering: Key Takeaways (the main points in 2-3 bullets), Background (the problem and context), Key Contributions (what's novel about this work), Methods (the approach taken), Results (main findings), Limitations (weaknesses of the study), Open Questions (gaps for future research), and Important Figures/Tables (which visuals to pay attention to). This should serve as a helpful guided reading before I dive into the paper myself.";

const STARTERS = [
    COMPREHENSIVE_OVERVIEW_DISPLAY,
    "What is the main research question or hypothesis of this paper?",
    "What methodology did the authors use?",
    "What are the key findings and conclusions?",
    "What are the limitations of this study?",
];

/** Suggested first questions; the overview chip sends a longer prompt than it shows. */
export function ChatStarters({ onPick }: { onPick: (prompt: string) => void }) {
    return (
        // Scrolls edge to edge of the panel; the first chip still lines up
        // with the composer.
        <div className="-mx-3">
            <Suggestions className="px-3">
                {STARTERS.map((q, i) => (
                    <Suggestion
                        key={i}
                        suggestion={q === COMPREHENSIVE_OVERVIEW_DISPLAY ? COMPREHENSIVE_OVERVIEW_PROMPT : q}
                        onClick={onPick}
                        className="animate-rise-in px-3 font-normal text-muted-foreground hover:text-foreground"
                        style={{ animationDelay: `${i * 40}ms` }}
                    >
                        {q}
                    </Suggestion>
                ))}
            </Suggestions>
        </div>
    );
}
