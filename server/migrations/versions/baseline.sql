
CREATE FUNCTION public.paper_content_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.ts_vector :=
                setweight(to_tsvector('pg_catalog.english', coalesce(NEW.title,'')), 'A') ||
                setweight(to_tsvector('pg_catalog.english', coalesce(NEW.raw_content,'')), 'D');
            RETURN NEW;
        END
        $$;

CREATE TABLE public.annotations (
    id uuid NOT NULL,
    highlight_id uuid NOT NULL,
    paper_id uuid NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid NOT NULL,
    role character varying NOT NULL
);

CREATE TABLE public.conversations (
    id uuid NOT NULL,
    title character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid,
    conversable_id uuid,
    conversable_type character varying NOT NULL,
    CONSTRAINT check_conversable_paper CHECK ((((conversable_type)::text = 'paper'::text) AND (conversable_id IS NOT NULL)))
);

CREATE TABLE public.discover_searches (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    question text NOT NULL,
    subqueries jsonb,
    results jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.documents (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    paper_id uuid,
    parent_document_id uuid,
    kind character varying DEFAULT 'note'::character varying NOT NULL,
    title character varying DEFAULT 'Untitled'::character varying NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.highlights (
    id uuid NOT NULL,
    paper_id uuid NOT NULL,
    raw_text text NOT NULL,
    start_offset integer,
    end_offset integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid,
    page_number integer,
    role character varying NOT NULL,
    type character varying,
    "position" jsonb,
    color character varying
);

CREATE TABLE public.messages (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying NOT NULL,
    content text NOT NULL,
    "references" jsonb,
    bucket jsonb,
    sequence integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid
);

CREATE TABLE public.paper_repos (
    id uuid NOT NULL,
    paper_id uuid NOT NULL,
    owner character varying NOT NULL,
    repo character varying NOT NULL,
    ref character varying,
    commit_sha character varying,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    error text,
    file_count integer,
    total_bytes bigint,
    storage_prefix character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.paper_tag_association (
    paper_id uuid NOT NULL,
    tag_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.paper_tags (
    id uuid NOT NULL,
    name character varying NOT NULL,
    color character varying,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.paper_upload_jobs (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    status character varying NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    task_id character varying,
    supplementary_of_paper_id uuid
);

CREATE TABLE public.papers (
    id uuid NOT NULL,
    file_url character varying NOT NULL,
    authors character varying[],
    title text,
    abstract text,
    institutions character varying[],
    keywords character varying[],
    publish_date timestamp without time zone,
    raw_content text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    user_id uuid,
    s3_object_key character varying,
    cached_presigned_url character varying,
    presigned_url_expires_at timestamp with time zone,
    status character varying NOT NULL,
    last_accessed_at timestamp with time zone DEFAULT now() NOT NULL,
    upload_job_id uuid,
    preview_url character varying,
    page_offset_map jsonb,
    doi character varying,
    size_in_kb integer,
    ts_vector tsvector,
    journal character varying,
    publisher character varying,
    attempted_metadata_at timestamp with time zone,
    parser text,
    ocr jsonb,
    figure_count integer,
    page_count integer,
    supplementary_of_paper_id uuid,
    generated_outline jsonb
);

CREATE TABLE public.project (
    id uuid NOT NULL,
    title character varying,
    description text,
    owner_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.project_paper (
    id uuid NOT NULL,
    paper_id uuid NOT NULL,
    project_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.sessions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    token character varying NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    user_agent character varying,
    ip_address character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE public.users (
    id uuid NOT NULL,
    email character varying NOT NULL,
    name character varying,
    picture character varying,
    is_active boolean,
    is_admin boolean,
    auth_provider character varying NOT NULL,
    provider_user_id character varying NOT NULL,
    locale character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_email_verified boolean NOT NULL,
    email_verification_token character varying,
    email_verification_expires_at timestamp with time zone
);

ALTER TABLE ONLY public.annotations
    ADD CONSTRAINT annotations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.discover_searches
    ADD CONSTRAINT discover_searches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.papers
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey1 PRIMARY KEY (id);

ALTER TABLE ONLY public.highlights
    ADD CONSTRAINT highlights_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.paper_repos
    ADD CONSTRAINT paper_repos_paper_id_key UNIQUE (paper_id);

ALTER TABLE ONLY public.paper_repos
    ADD CONSTRAINT paper_repos_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.paper_tag_association
    ADD CONSTRAINT paper_tag_association_pkey PRIMARY KEY (paper_id, tag_id);

ALTER TABLE ONLY public.paper_tags
    ADD CONSTRAINT paper_tags_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.paper_upload_jobs
    ADD CONSTRAINT paper_upload_jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.project_paper
    ADD CONSTRAINT project_paper_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.project
    ADD CONSTRAINT project_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

CREATE INDEX ix_documents_paper_user ON public.documents USING btree (paper_id, user_id);

CREATE INDEX ix_documents_parent ON public.documents USING btree (parent_document_id);

CREATE INDEX ix_paper_repos_paper_id ON public.paper_repos USING btree (paper_id);

CREATE INDEX ix_papers_supplementary_of_paper_id ON public.papers USING btree (supplementary_of_paper_id);

CREATE INDEX ix_papers_ts_vector ON public.papers USING gin (ts_vector);

CREATE UNIQUE INDEX ix_sessions_token ON public.sessions USING btree (token);

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);

CREATE INDEX ix_users_provider_user_id ON public.users USING btree (provider_user_id);

CREATE UNIQUE INDEX ux_documents_main_per_paper ON public.documents USING btree (paper_id, user_id) WHERE (((kind)::text = 'main'::text) AND (paper_id IS NOT NULL));

CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE ON public.papers FOR EACH ROW EXECUTE FUNCTION public.paper_content_trigger();

ALTER TABLE ONLY public.annotations
    ADD CONSTRAINT annotations_highlight_id_fkey FOREIGN KEY (highlight_id) REFERENCES public.highlights(id);

ALTER TABLE ONLY public.annotations
    ADD CONSTRAINT annotations_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.annotations
    ADD CONSTRAINT annotations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.discover_searches
    ADD CONSTRAINT discover_searches_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_parent_document_id_fkey FOREIGN KEY (parent_document_id) REFERENCES public.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.papers
    ADD CONSTRAINT documents_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_user_id_fkey1 FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.highlights
    ADD CONSTRAINT highlights_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.highlights
    ADD CONSTRAINT highlights_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.paper_repos
    ADD CONSTRAINT paper_repos_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.paper_tag_association
    ADD CONSTRAINT paper_tag_association_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.paper_tag_association
    ADD CONSTRAINT paper_tag_association_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.paper_tags(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.paper_tags
    ADD CONSTRAINT paper_tags_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.paper_upload_jobs
    ADD CONSTRAINT paper_upload_jobs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.papers
    ADD CONSTRAINT papers_supplementary_of_paper_id_fkey FOREIGN KEY (supplementary_of_paper_id) REFERENCES public.papers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.papers
    ADD CONSTRAINT papers_upload_job_id_fkey FOREIGN KEY (upload_job_id) REFERENCES public.paper_upload_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.project
    ADD CONSTRAINT project_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);

ALTER TABLE ONLY public.project_paper
    ADD CONSTRAINT project_paper_paper_id_fkey FOREIGN KEY (paper_id) REFERENCES public.papers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.project_paper
    ADD CONSTRAINT project_paper_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.project(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

