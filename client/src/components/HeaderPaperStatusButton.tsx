"use client";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getStatusIcon, PaperStatusEnum } from "@/components/utils/PdfStatus";
import { paperAtom } from "@/components/paper/paperStore";
import { usePaperAtomValue } from "@/components/paper/PaperStoreProvider";
import { useUpdatePaperStatus } from "@/components/paper/useUpdatePaperStatus";

export function HeaderPaperStatusButton() {
    const paperStatus = usePaperAtomValue(paperAtom)?.status;
    const updatePaperStatus = useUpdatePaperStatus();
    if (!paperStatus) return null;
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" className="h-8 gap-1.5 px-2 text-xs max-md:w-8 max-md:px-0 max-md:has-[>svg]:px-0" aria-label={`Status: ${paperStatus}`}>
                    {getStatusIcon(paperStatus)}
                    <span className="capitalize hidden md:inline">{paperStatus}</span>
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => updatePaperStatus(PaperStatusEnum.TODO)}>
                    {getStatusIcon(PaperStatusEnum.TODO)}
                    <span className="ml-2">Todo</span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => updatePaperStatus(PaperStatusEnum.READING)}>
                    {getStatusIcon(PaperStatusEnum.READING)}
                    <span className="ml-2">Reading</span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => updatePaperStatus(PaperStatusEnum.COMPLETED)}>
                    {getStatusIcon(PaperStatusEnum.COMPLETED)}
                    <span className="ml-2">Completed</span>
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
