'use client';

import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { FileText, Highlighter, MessageCircle } from 'lucide-react';
import { LayoutGroup, motion } from 'motion/react';
import { PILL_SPRING } from '@/lib/motion';
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
                role="toolbar"
                aria-orientation="vertical"
                aria-label="Side panel"
                className={cn(
                    'absolute right-2 z-20 flex flex-col gap-1 p-1 bg-background/90 backdrop-blur-md border border-border rounded-xl shadow-md transition-[top] duration-200 ease-out-soft dark:bg-zinc-900/90 dark:border-zinc-700 dark:shadow-black/40',
                    toolbarTopClass,
                )}
            >
                <LayoutGroup id="paper-side-rail">
                    {TOOLS.map((item) => {
                        const active = item.name === tab;
                        return (
                            <Tooltip key={item.name}>
                                <TooltipTrigger asChild>
                                    <button
                                        type="button"
                                        aria-pressed={active}
                                        className={cn(
                                            'relative isolate flex h-8 w-8 items-center justify-center rounded-lg transition-colors duration-150',
                                            active
                                                ? 'text-brand-foreground'
                                                : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                                        )}
                                        onClick={() => setTab(item.name)}
                                        aria-label={item.label}
                                    >
                                        {active && (
                                            <motion.span
                                                layoutId="pill"
                                                transition={PILL_SPRING}
                                                className="absolute inset-0 -z-10 rounded-lg bg-brand shadow-sm"
                                            />
                                        )}
                                        <item.icon className="h-[18px] w-[18px]" />
                                    </button>
                                </TooltipTrigger>
                                <TooltipContent side="left" sideOffset={8}>
                                    {item.label}
                                </TooltipContent>
                            </Tooltip>
                        );
                    })}
                </LayoutGroup>
            </div>
        </TooltipProvider>
    );
}
