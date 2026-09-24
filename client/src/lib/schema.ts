import type { Schemas } from "@/lib/api/client";

/**
 * Names for the API types the app passes around. Everything describing server
 * data is an alias of the generated OpenAPI schema (`lib/api/schema.d.ts`,
 * regenerated with `yarn gen:api`); only client-side state is hand-written.
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
export type ModelSlot = Schemas["ModelSlotOut"];
export type ModelSettings = Schemas["ModelSettingsOut"];

/** `GET /api/paper?id=` */
export type PaperData = Schemas["PaperDetail"];

/** Item of `GET /api/paper/all`. */
export type LibraryPaper = Schemas["LibraryPaper"];

/** Item of `GET /api/projects/papers/{project_id}`. */
export type ProjectPaper = Schemas["ProjectPaperItem"];

/**
 * A paper row as the shared list components take it: an item from either
 * list route. Fields only one route sends (`tags` / `preview_url` /
 * `size_in_kb` on the library; `journal` / `doi` / `publisher` on project
 * papers) are optional on both.
 */
export type PaperItem = (LibraryPaper | ProjectPaper) &
    Partial<Omit<LibraryPaper, keyof ProjectPaper>> &
    Partial<Omit<ProjectPaper, keyof LibraryPaper>>;

/** `GET /api/projects`, `GET /api/projects/{project_id}` */
export type Project = Schemas["ProjectResponse"];
