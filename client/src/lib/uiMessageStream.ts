import { Reference } from "@/lib/schema";

const EVIDENCE_START = "---EVIDENCE---";
const EVIDENCE_END = "---END-EVIDENCE---";

/**
 * Strip the trailing `---EVIDENCE---...---END-EVIDENCE---` block from a
 * streamed assistant message. The agent emits citations inline so they can
 * be parsed once the run completes; the user shouldn't see the raw block
 * in the chat UI. Tolerates an unterminated block by trimming everything
 * from the start delimiter onward.
 */
export function stripEvidenceBlock(text: string): string {
    const start = text.indexOf(EVIDENCE_START);
    if (start === -1) return text;
    const after = text.slice(start + EVIDENCE_START.length);
    const endRel = after.indexOf(EVIDENCE_END);
    const tail = endRel === -1 ? "" : after.slice(endRel + EVIDENCE_END.length);
    return (text.slice(0, start) + tail).trimEnd();
}

type UIMessageChunk =
    | { type: "text-delta"; delta: string }
    | { type: "reasoning-delta"; delta: string }
    | { type: "data-references"; data: Reference }
    | { type: "data-references_reconciled"; data: Reference }
    | { type: "data-status"; data: unknown }
    | { type: "error"; errorText: string }
    | { type: string; [key: string]: unknown };

interface ReadUIMessageStreamHandlers {
    onText?: (delta: string) => void;
    onReasoning?: (delta: string) => void;
    onReferences?: (references: Reference) => void;
    onStatus?: (status: string | null) => void;
}

export async function readOpenPaperUIMessageStream(
    stream: ReadableStream<Uint8Array>,
    handlers: ReadUIMessageStreamHandlers
) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const frames = buffer.split("\n\n");
            buffer = frames.pop() || "";

            for (const frame of frames) {
                const data = frame
                    .split("\n")
                    .filter((line) => line.startsWith("data:"))
                    .map((line) => line.slice(5).trimStart())
                    .join("\n");

                if (!data || data === "[DONE]") continue;

                const chunk = JSON.parse(data) as UIMessageChunk;
                switch (chunk.type) {
                    case "text-delta":
                        if (typeof chunk.delta === "string") {
                            handlers.onText?.(chunk.delta);
                        }
                        break;
                    case "reasoning-delta":
                        if (typeof chunk.delta === "string") {
                            handlers.onReasoning?.(chunk.delta);
                        }
                        break;
                    case "data-references":
                    case "data-references_reconciled":
                        handlers.onReferences?.(chunk.data as Reference);
                        break;
                    case "data-status":
                        handlers.onStatus?.(
                            typeof chunk.data === "string" ? chunk.data : null
                        );
                        break;
                    case "error":
                        throw new Error(`Server error: ${chunk.errorText}`);
                    default:
                        break;
                }
            }
        }
    } finally {
        reader.releaseLock();
    }
}
