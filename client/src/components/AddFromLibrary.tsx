
"use client";

import { api, unwrap } from "@/lib/api/client";
import { toast } from "sonner";
import { LibraryTable, type LibraryPaper } from "./LibraryTable";

interface AddFromLibraryProps {
    projectId: string;
    onPapersAdded: () => void;
    projectPaperIds?: string[];
    onUploadClick?: () => void;
}

export default function AddFromLibrary({ projectId, onPapersAdded, projectPaperIds, onUploadClick }: AddFromLibraryProps) {

    const handleAddPapers = (papers: LibraryPaper[], action: string) => {
        if (action !== "Add") return;

        const paperIds = papers.map(p => p.id);

        unwrap(api.POST("/api/projects/papers/{project_id}", {
            params: { path: { project_id: projectId } },
            body: { paper_ids: paperIds },
        }))
            .then(() => {
                toast.success("Papers added to project successfully!");
                onPapersAdded();
            })
            .catch(error => {
                console.error("Failed to add papers to project", error);
                toast.error("Failed to add papers to project.");
            });
    };

    return (
        <LibraryTable
            selectable={true}
            actionOptions={["Add"]}
            onSelectFiles={handleAddPapers}
            projectPaperIds={projectPaperIds}
            onUploadClick={onUploadClick}
        />
    );
}
