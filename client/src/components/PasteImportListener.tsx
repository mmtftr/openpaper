"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { importPaperLinkWithToast, isEditableTarget, parsePaperLink } from "@/lib/paperLink";

/**
 * Paste an arXiv / PDF link anywhere on a (main) page — outside a text field
 * or dialog — to import it. On a project page it joins that project.
 */
export function PasteImportListener() {
    const router = useRouter();
    const pathname = usePathname();
    const { user } = useAuth();

    useEffect(() => {
        if (!user) return;
        const onPaste = (e: ClipboardEvent) => {
            if (e.defaultPrevented || isEditableTarget(e.target)) return;
            // Dialogs (upload, ⌘K, ...) handle their own pastes.
            if (e.target instanceof Element && e.target.closest('[role="dialog"], [role="alertdialog"]')) return;
            const link = parsePaperLink(e.clipboardData?.getData("text/plain") ?? "");
            if (!link) return;
            e.preventDefault();
            const projectId = /^\/projects\/([^/]+)/.exec(pathname)?.[1];
            void importPaperLinkWithToast(link, {
                projectId,
                open: (paperId) => router.push(`/paper/${paperId}`),
            });
        };
        document.addEventListener("paste", onPaste);
        return () => document.removeEventListener("paste", onPaste);
    }, [router, pathname, user]);

    return null;
}
