import { PaperStatus } from "@/components/utils/PdfStatus";
import { BasicUser } from "./auth";

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
    summary: string;
    institutions: string[];
    keywords: string[];
    starter_questions: string[];
    is_public: boolean;
    share_id: string;
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

export interface SharedPaper {
    paper: PaperData;
    highlights: PaperHighlight[];
    annotations: PaperHighlightAnnotation[];
    owner: BasicUser;
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
    is_owner?: boolean;
    owner_picture?: string;
    owner_name?: string;
}


export enum JobStatus {
    PENDING = 'pending',
    RUNNING = 'running',
    COMPLETED = 'completed',
    FAILED = 'failed',
    CANCELLED = 'cancelled'
}

export type JobStatusType = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export const SubscriptionStatus = {
    ACTIVE: 'active',
    CANCELED: 'canceled',
    PAST_DUE: 'past_due',
    INCOMPLETE: 'incomplete',
    TRIALING: 'trialing',
    UNPAID: 'unpaid',
} as const;

export type SubscriptionStatusType = typeof SubscriptionStatus[keyof typeof SubscriptionStatus];

export interface UserSubscription {
    has_subscription: boolean;
    had_subscription: boolean;
    requires_payment_update: boolean;
    subscription: {
        status: SubscriptionStatusType;
        interval: "month" | "year";
        current_period_end: string;
        current_period_start: string;
        cancel_at_period_end: boolean;
    };
    scheduled_change?: {
        new_interval: "month" | "year";
        effective_date: string;
    } | null;
}

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

export interface PaperImage {
    paper_id: string;
    s3_object_key: string;
    image_url: string;
    format: string;
    size_bytes: number;
    width: number;
    height: number;
    page_number: number;
    image_index: number;
    caption: string | null;
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
    summary?: string
    created_at?: string
    publish_date?: string
    status?: PaperStatus
    preview_url?: string
    file_url?: string
    size_in_kb?: number
    tags?: PaperTag[]
    is_owner?: boolean
    journal?: string
    doi?: string
    publisher?: string
}

export interface CreditUsage {
    used: number;
    remaining: number;
    total: number;
    usagePercentage: number;
    showWarning: boolean;
    isNearLimit: boolean;
    isCritical: boolean;
}

export interface AudioOverview {
    id: string;
    conversable_id: string;
    conversable_type: string;
    audio_url: string;
    transcript: string;
    title: string;
    citations: ReferenceCitation[];
    created_at: string;
    updated_at: string;
    job_id: string;
}

export interface AudioOverviewJob extends JobStatusResponse {
    id: string;
    conversable_id: string;
    conversable_type: string;
    status_message: string | null;
}

export interface Project {
    id: string;
    title: string;
    description: string;
    num_papers?: number;
    num_conversations?: number;
    num_audio_overviews?: number;
    num_data_tables?: number;
    created_at: string;
    updated_at: string;
    role?: ProjectRole;
    num_roles?: number;
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

export enum ProjectRole {
    Admin = 'admin',
    Editor = 'editor',
    Viewer = 'viewer',
}

export interface Collaborator {
    id: string;
    name: string;
    picture: string;
    email: string;
    role: ProjectRole;
}

export interface PendingInvite {
    id?: string;
    email: string;
    role: ProjectRole;
    invited_at: string;
}

export interface ProjectInvitation {
    id: string;
    project_id: string;
    project_name: string;
    invited_by: string;
    email: string;
    role: string;
    accepted_at?: string;
    invited_at: string;
}

export interface DataTableJob {
    id: string;
    project_id: string | null;
    columns: string[] | null;
    task_id: string | null;
    title: string | null;
    status: JobStatusType;
    started_at: string | null;
    completed_at: string | null;
    created_at: string | null;
    updated_at: string | null;
    error_message: string | null;
    result_id: string | null;
}

// Response from /api/projects/tables/{job_id} status endpoint
export interface DataTableJobStatusResponse extends JobStatusResponse {
    columns: string[] | null;
    task_id: string | null;
    error_message: string | null;
    celery_status: string | null;
    celery_progress_message: string | null;
    celery_error: string | null;
}

export interface DataTableCellValue {
    value: string;
    citations: ReferenceCitation[];
}

export interface DataTableRow {
    id: string;
    paper_id: string;
    values: {
        [columnName: string]: DataTableCellValue;
    };
}

export interface DataTableResult {
    success: boolean;
    title: string;
    columns: string[];
    rows: DataTableRow[];
    row_failures: string[] | null;
    created_at: string | null;
}

export interface SubscriptionLimits {
    paper_uploads: number;
    knowledge_base_size: number;
    chat_credits_weekly: number;
    audio_overviews_weekly: number;
    data_tables_weekly: number;
    discover_searches_weekly: number;
    projects: number;
    model: string[];
}

export interface SubscriptionUsage {
    paper_uploads: number;
    paper_uploads_remaining: number;
    knowledge_base_size: number;
    knowledge_base_size_remaining: number;
    chat_credits_used: number;
    chat_credits_remaining: number;
    audio_overviews_used: number;
    audio_overviews_remaining: number;
    projects: number;
    projects_remaining: number;
    data_tables_used: number;
    data_tables_remaining: number;
    discover_searches_used: number;
    discover_searches_remaining: number;
}

export interface SubscriptionData {
    plan: 'basic' | 'researcher';
    limits: SubscriptionLimits;
    usage: SubscriptionUsage;
}

export interface UseSubscriptionReturn {
    subscription: SubscriptionData | null;
    loading: boolean;
    error: string | null;
    refetch: () => Promise<void>;
}
