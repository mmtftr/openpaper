import { toast } from "sonner";
import { mutate as mutateGlobal } from "swr";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import { revalidatePaperLists } from "@/hooks/usePapers";

/**
 * A pasted link to a paper: an arXiv page / id, or a direct PDF URL. The
 * server (`POST /api/paper/upload/import`) re-parses `text`, downloads the
 * PDF and returns the library paper if it was already imported.
 */
export type PaperLink =
    | { kind: "arxiv"; text: string; id: string; version: string; label: string }
    | { kind: "pdf"; text: string; label: string };

// Mirrors `arxiv_link` in server/app/ingest/metadata_ids.py.
const ARXIV_ID = String.raw`(\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[a-z]{2})?\/\d{2}(?:0[1-9]|1[0-2])\d{3})(v\d+)?`;
const ARXIV_LINK = new RegExp(
    String.raw`^(?:https?:\/\/)?(?:[\w-]+\.)*(?:(?:arxiv|alphaxiv)\.org\/(?:abs|pdf|html|overview|format)\/|(?:dx\.)?doi\.org\/10\.48550\/arxiv\.)` +
        ARXIV_ID +
        String.raw`(?:\.pdf)?\/?(?:[?#].*)?$`,
    "i",
);
const ARXIV_MARKED = new RegExp(String.raw`^arxiv\s*:\s*` + ARXIV_ID + "$", "i");
const ARXIV_BARE = new RegExp(`^${ARXIV_ID}$`, "i");

function normalizeArxivId(id: string): string {
    if (!id.includes("/")) return id;
    const [archive, number] = id.split("/");
    const [name, subject] = archive.split(".");
    return `${name.toLowerCase()}${subject ? `.${subject.toUpperCase()}` : ""}/${number}`;
}

/**
 * The paper `text` links to, or null. `bareIds` also accepts a bare arXiv id
 * ("2504.11844") — right in a search box, too loose for a global paste.
 */
export function parsePaperLink(text: string, { bareIds = false } = {}): PaperLink | null {
    const trimmed = text.trim();
    if (!trimmed || trimmed.length > 2048 || /\s/.test(trimmed)) return null;

    const arxiv =
        ARXIV_LINK.exec(trimmed) ?? ARXIV_MARKED.exec(trimmed) ?? (bareIds ? ARXIV_BARE.exec(trimmed) : null);
    if (arxiv) {
        const id = normalizeArxivId(arxiv[1]);
        const version = (arxiv[2] ?? "").toLowerCase();
        return { kind: "arxiv", text: trimmed, id, version, label: `arXiv ${id}${version}` };
    }

    let url: URL;
    try {
        url = new URL(trimmed);
    } catch {
        return null;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    // "…/paper.pdf", or a /pdf endpoint (openreview.net/pdf?id=…).
    const last = url.pathname.replace(/\/+$/, "").split("/").pop() ?? "";
    if (!/\.pdf$/i.test(last) && last.toLowerCase() !== "pdf") return null;
    let name = last;
    try {
        name = decodeURIComponent(last);
    } catch {
        // keep the raw segment
    }
    return { kind: "pdf", text: trimmed, label: /\.pdf$/i.test(name) ? name : url.hostname };
}

const inFlight = new Map<string, Promise<Schemas["ImportedPaper"]>>();

/** Import `link` (one request per link at a time, however often it's pasted). */
export function importPaperLink(link: PaperLink, projectId?: string): Promise<Schemas["ImportedPaper"]> {
    const key = `${link.kind === "arxiv" ? link.id : link.text}|${projectId ?? ""}`;
    const pending = inFlight.get(key);
    if (pending) return pending;
    const request = unwrap(
        api.POST("/api/paper/upload/import", {
            params: { query: { project_id: projectId } },
            body: { url: link.text },
        }),
    ).finally(() => inFlight.delete(key));
    inFlight.set(key, request);
    return request;
}

/**
 * Import with a progress toast. `navigate` opens the paper when done (an
 * explicit "Import" action); otherwise the toast offers "Open" (a paste,
 * which shouldn't yank the page away). Resolves null after an error toast.
 */
export async function importPaperLinkWithToast(
    link: PaperLink,
    { projectId, open, navigate = false }: { projectId?: string; open: (paperId: string) => void; navigate?: boolean },
): Promise<Schemas["ImportedPaper"] | null> {
    const toastId = toast.loading(`Importing ${link.label}…`);
    try {
        const paper = await importPaperLink(link, projectId);
        void revalidatePaperLists();
        if (projectId) {
            void mutateGlobal(
                (key) => Array.isArray(key) && key[0] === "/api/projects/papers/{project_id}" && key[1] === projectId,
            );
        }
        const message = paper.existing ? "Already in your library" : `Imported ${link.label}`;
        const description = paper.existing
            ? (paper.title ?? link.label)
            : "Processing continues in the background.";
        if (navigate) {
            toast.success(message, { id: toastId, description });
            open(paper.paper_id);
        } else {
            toast.success(message, {
                id: toastId,
                description,
                duration: 8000,
                action: { label: "Open", onClick: () => open(paper.paper_id) },
            });
        }
        return paper;
    } catch (error) {
        console.error("Import failed", link.text, error);
        toast.error(`Couldn't import ${link.label}`, {
            id: toastId,
            description: error instanceof Error ? error.message : undefined,
        });
        return null;
    }
}

/** Whether a paste landing on `target` belongs to a text field. */
export function isEditableTarget(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    if (target.isContentEditable) return true;
    const tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}
