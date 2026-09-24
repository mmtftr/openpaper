import { PaperStatus } from "@/components/utils/PdfStatus";

export type HighlightType = 'topic' | 'motivation' | 'method' | 'evidence' | 'result' | 'impact' | 'general';

export interface ReferenceCitation {
    index: number;
    text: string;
    paper_id?: string;
}

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

export interface SupplementaryMaterialSummary {
    id: string;
    title: string | null;
    preview_url: string | null;
    page_count: number | null;
    created_at: string;
    status: 'pending' | 'running' | 'completed' | 'failed' | string;
}

export interface ChatMessage {
    id?: string;
    role: 'user' | 'assistant';
    content: string;
    references?: Reference;
    reasoning?: string;
}

// Position types for react-pdf-highlighter-extended
export interface ScaledRect {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    width: number;
    height: number;
    pageNumber: number;
}

export interface ScaledPosition {
    boundingRect: ScaledRect;
    rects: ScaledRect[];
    usePdfCoordinates?: boolean;
}

export type HighlightColor = 'yellow' | 'green' | 'blue' | 'pink' | 'purple';

export interface PaperHighlight {
    id?: string;
    raw_text: string;
    role: 'user' | 'assistant';
    start_offset?: number;
    end_offset?: number;
    page_number?: number;
    type?: HighlightType;
    position?: ScaledPosition;
    color?: HighlightColor;
}

export interface PaperHighlightAnnotation {
    id: string;
    highlight_id: string;
    paper_id: string;
    content: string;
    role: 'user' | 'assistant';
    created_at: string;
}

export interface Reference {
    citations: Citation[];
}

export interface Citation {
    key: string;
    paper_id?: string;
    reference: string;
    // Set by the agentic chat: 1-indexed page the quote was taken from.
    // The PDF highlighter uses this to pin its search to the right page.
    page?: number;
    // "normalizer" | "llm" — set when the quote text was rewritten from the
    // OCR form to a pymupdf-grounded form for highlighter compatibility.
    matched_via?: string;
    // ---- Code citations (repo inspection) ------------------------------
    // Present INSTEAD of `page`: the quote came from the paper's connected
    // repo snapshot, not the PDF. The PDF highlight path must skip these.
    file?: string;
    start_line?: number | null;
    end_line?: number | null;
    // SHA-pinned GitHub permalink; only attached once the quote verified.
    github_url?: string | null;
    // false when the quoted snippet couldn't be found at the claimed lines.
    verified?: boolean;
}

export interface Conversation {
    id: string;
    title: string;
    updated_at: string;
}


export enum JobStatus {
    PENDING = 'pending',
    RUNNING = 'running',
    COMPLETED = 'completed',
    FAILED = 'failed',
    CANCELLED = 'cancelled'
}

export type JobStatusType = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface HighlightResult {
    id: string;
    raw_text: string;
    start_offset: number | null;
    end_offset: number | null;
    page_number: number | null;
    role: string;
    created_at: string;
    type?: HighlightType;
}

export interface AnnotationResult {
    id: string;
    content: string;
    role: string;
    created_at: string;
    highlight: HighlightResult;
}

export interface PaperResult {
    id: string;
    title: string | null;
    authors: string[] | null;
    abstract: string | null;
    status: string;
    publish_date: string | null;
    created_at: string;
    last_accessed_at: string;
    highlights: HighlightResult[];
    annotations: AnnotationResult[];
    preview_url: string | null;
}

export interface SearchResults {
    papers: PaperResult[];
    total_papers: number;
    total_highlights: number;
    total_annotations: number;
}

export interface JobStatusResponse {
    job_id: string;
    status: JobStatusType;
    title: string | null;
    started_at: string;
    created_at: string;
    completed_at: string | null;
}

export interface PaperUploadJobStatusResponse extends JobStatusResponse {
    paper_id: string | null;
    has_file_url: boolean;
    has_metadata: boolean;
    celery_progress_message: string | null;
}

export interface PaperTag {
    id: string;
    name: string;
    color: string;
}

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

export interface Project {
    id: string;
    title: string;
    description: string;
    num_papers?: number;
    created_at: string;
    updated_at: string;
}

export interface PdfUploadResponse {
    message: string;
    job_id: string;
    file_name?: string;
}

export interface MinimalJob {
    jobId: string;
    fileName: string;
}

