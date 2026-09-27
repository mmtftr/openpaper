"use client";

import { HeaderBackLink } from "@/components/AppHeader";
import { usePaperProjects } from "@/components/PaperProjects";
import { parentPaperIdAtom } from "./paperStore";
import { usePaperAtomValue } from "./PaperStoreProvider";

/**
 * The paper header's way back: the paper's project when it is in one (the
 * most recently updated, if several), otherwise the library.
 */
export function PaperBreadcrumb() {
    const paperId = usePaperAtomValue(parentPaperIdAtom);
    const { data: projects } = usePaperProjects(paperId || null);
    const project = projects?.reduce<(typeof projects)[number] | null>(
        (latest, p) => (!latest || p.updated_at > latest.updated_at ? p : latest),
        null
    );
    if (project) {
        return <HeaderBackLink href={`/projects/${project.id}`} label={project.title || "Project"} />;
    }
    return <HeaderBackLink href="/papers" label="Library" />;
}
