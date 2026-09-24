import type { Schemas } from "@/lib/api/client";
import type { PaperStatus } from "@/components/utils/PdfStatus";

/**
 * Names for the API types the app passes around. Everything describing server
 * data is an alias of the generated OpenAPI schema (`lib/api/schema.d.ts`,
 * regenerated with `yarn gen:api`); only client-side state is hand-written.
 *
 * The block marked "Compat" at the bottom is the exception: hand-written
 * shapes kept only because pages / top-level components not yet moved to the
 * generated types still compile against them. New code should use
 * `Schemas[...]` (noted on each) instead.
 */

export interface ReferenceCitation {
    index: number;
    text: string;
    paper_id?: string;
}

/** `GET /api/paper/{paper_id}/supplementary` */
export type SupplementaryMaterialSummary = Schemas["SupplementaryMaterialItem"];

// Position types for react-pdf-highlighter-extended
export type ScaledRect = Schemas["ScaledRect"];
export type ScaledPosition = Schemas["ScaledPosition"];

export type HighlightColor = NonNullable<Schemas["HighlightResponse"]["color"]>;

/**
 * A highlight as the reader holds it: the server's `HighlightResponse`, with
 * the server-assigned fields optional because the reader also builds
 * highlights locally before (or without) saving them.
 */
export type PaperHighlight = Omit<
    Schemas["HighlightResponse"],
    "id" | "paper_id" | "created_at" | "updated_at"
> &
    Partial<Pick<Schemas["HighlightResponse"], "id" | "paper_id" | "created_at" | "updated_at">>;

/**
 * `AnnotationResponse`, with the bookkeeping fields optional so a local-only
 * note (the reader benchmark) can be built without them.
 */
export type PaperHighlightAnnotation = Omit<Schemas["AnnotationResponse"], "updated_at" | "user_id"> &
    Partial<Pick<Schemas["AnnotationResponse"], "updated_at" | "user_id">>;

export interface Reference {
    citations: Citation[];
}

/**
 * A chat citation (`data-citations` part) with its `key` normalized to a
 * string — see `citationsFromMessage`. Code citations (repo inspection) carry
 * `file`/`start_line`/`end_line` instead of `page`.
 */
export type Citation = Omit<Schemas["ChatCitation"], "key"> & { key: string };

/** `GET /api/paper/conversations` */
export type Conversation = Schemas["PaperConversationSummary"];

export type JobStatusType = Schemas["JobStatus"];

export type PaperResult = Schemas["PaperResult"];
export type SearchResults = Schemas["SearchResults"];

export type PaperTag = Schemas["PaperTagResponse"];

/** An upload the client is tracking until its job finishes. */
export interface MinimalJob {
    jobId: string;
    fileName: string;
}

// Settings -> Models (GET/PUT /api/settings/models)
export type ReasoningEffort = NonNullable<Schemas["ModelSlotUpdate"]["reasoning_effort"]>;
export type ModelSlotUpdate = Schemas["ModelSlotUpdate"];
export type ModelProvider = Schemas["ProviderOut"];
export type SelectableModel = Schemas["SelectableModel"];

// ---------------------------------------------------------------------------
// Compat: hand-written shapes still used by pages / top-level components that
// don't compile against the generated types yet (mostly `string | null` where
// these say `string | undefined`). Delete each once its last user moves to the
// generated type named in its comment.
// ---------------------------------------------------------------------------

/** Compat — use `Schemas["PaperDetail"]` (`GET /api/paper?id=`). */
export interface PaperData {
    filename: string;
    file_url: string;
    authors: string[];
    title: string;
    abstract: string;
    publish_date: string;
    institutions: string[];
    keywords: string[];
    status: PaperStatus;
    journal?: string;
    doi?: string;
    publisher?: string;
    // Mistral OCR pipeline. parser is "mistral" | "pymupdf" — selects which
    // chat context modes the picker exposes for this paper.
    parser?: "mistral" | "pymupdf";
    page_count?: number;
    figure_count?: number;
    supplementary_of_paper_id?: string | null;
}

/** Compat — use `Schemas["UploadJobStatusResponse"]`. */
export interface PaperUploadJobStatusResponse {
    job_id: string;
    status: JobStatusType;
    started_at: string;
    completed_at?: string | null;
    paper_id: string | null;
    has_file_url: boolean;
    has_metadata: boolean;
    celery_progress_message: string | null;
}

/**
 * Compat — use `Schemas["LibraryPaper"]` (`GET /api/paper/all`) or
 * `Schemas["ProjectPaperItem"]` (`GET /api/projects/papers/{id}`).
 */
export interface PaperItem {
    id: string
    title: string
    abstract?: string
    authors?: string[]
    keywords?: string[]
    institutions?: string[]
    created_at?: string
    publish_date?: string
    status?: PaperStatus
    preview_url?: string
    file_url?: string
    size_in_kb?: number
    tags?: PaperTag[]
    journal?: string
    doi?: string
    publisher?: string
}

/** Compat — use `Schemas["ProjectResponse"]`. */
export interface Project {
    id: string;
    title: string;
    description: string;
    num_papers?: number;
    created_at: string;
    updated_at: string;
}

/** Compat — use `Schemas["SlotChoice"]`. */
interface SlotChoice {
    provider: string;
    model: string;
    model_name: string;
    reasoning_effort: string | null;
}

/** Compat — use `Schemas["ModelSlotOut"]`. */
export interface ModelSlot {
    slot: string;
    description: string;
    role: "default" | "fast";
    default: SlotChoice;
    override: {
        provider: string | null;
        model: string | null;
        reasoning_effort: string | null;
        updated_at: string | null;
    } | null;
    // Set when the stored override no longer resolves; the default is used.
    override_error: string | null;
    effective: SlotChoice;
}

/** Compat — use `Schemas["ModelSettingsOut"]`. */
export interface ModelSettings {
    slots: ModelSlot[];
    providers: ModelProvider[];
    models: SelectableModel[];
}
