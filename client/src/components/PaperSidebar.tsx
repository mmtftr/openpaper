'use client';

import { Button } from '@/components/ui/button';
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { FileText, Highlighter, MessageCircle } from 'lucide-react';
import type { ComponentType } from 'react';
import {
    SIDE_PANEL_TOOLS,
    sidePanelTabAtom,
    type SidePanelTab,
    type SidePanelTool,
} from '@/components/paper/paperStore';
import { usePaperAtom } from '@/components/paper/PaperStoreProvider';

/** Tooltip / accessible name and icon per tool. */
const TOOL_BUTTONS: Record<SidePanelTool, { label: string; icon: ComponentType<{ className?: string }> }> = {
    Chat: { label: 'Show chat', icon: MessageCircle },
    Annotations: { label: 'All annotations', icon: Highlighter },
    Doc: { label: 'Notes', icon: FileText },
};
const TOOLS = SIDE_PANEL_TOOLS.map((name) => ({ name, ...TOOL_BUTTONS[name] }));

/** Primary panels where the toolbar sits higher (less gap under the top bar). */
const COMPACT_TOP_OFFSET_TOOLS = new Set<SidePanelTab>(['Chat', 'Annotations']);

/** The floating tab switcher on the side panel's right edge. */
export function PaperSidebar() {
    const [tab, setTab] = usePaperAtom(sidePanelTabAtom);
    const toolbarTopClass = COMPACT_TOP_OFFSET_TOOLS.has(tab) ? 'top-2' : 'top-14';

    return (
        <TooltipProvider>
            <div
                className={cn(
                    'absolute right-2 z-20 flex flex-col gap-1 p-1 bg-background/95 backdrop-blur-sm border border-border rounded-lg shadow-lg transition-[top] duration-200 dark:bg-zinc-900/95 dark:border-zinc-600 dark:ring-1 dark:ring-white/10 dark:shadow-black/40',
                    toolbarTopClass,
                )}
            >
                {TOOLS.map((item) => (
                    <Tooltip key={item.name}>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                className={`h-8 w-8 p-0 rounded-md ${
                                    item.name === tab
                                        ? 'bg-blue-500 text-blue-100 hover:bg-blue-600 dark:bg-blue-600 dark:text-white dark:hover:bg-blue-500'
                                        : 'text-secondary-foreground hover:bg-blue-100 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-foreground'
                                }`}
                                onClick={() => setTab(item.name)}
                                aria-label={item.label}
                            >
                                <item.icon className="h-5 w-5" />
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent side="left" sideOffset={8}>
                            {item.label}
                        </TooltipContent>
                    </Tooltip>
                ))}
            </div>
        </TooltipProvider>
    );
}
