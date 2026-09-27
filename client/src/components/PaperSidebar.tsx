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
import { useEffect, useRef, useState, type ComponentType } from 'react';
import {
    SIDE_PANEL_TOOLS,
    sidePanelTabAtom,
    toggleReadModeAtom,
    type SidePanelTab,
    type SidePanelTool,
} from '@/components/paper/paperStore';
import { usePaperAtom, useSetPaperAtom } from '@/components/paper/PaperStoreProvider';

const TOOL_META: Record<SidePanelTool, { label: string; icon: ComponentType<{ className?: string }> }> = {
    Chat: { label: 'Chat', icon: MessageCircle },
    Annotations: { label: 'Highlights', icon: Highlighter },
    Doc: { label: 'Notes', icon: FileText },
};

const orderFor = (first: SidePanelTool): SidePanelTool[] => [
    first,
    ...SIDE_PANEL_TOOLS.filter((tool) => tool !== first),
];

/**
 * Centres the switcher's first icon on the ~40px bar it sits on: each tool's
 * top bar, or the reader toolbar in read mode (taller on touch screens).
 */
const topClass = (tab: SidePanelTab) => (tab === 'Read' ? 'top-0.5 pointer-coarse:top-1.5' : 'top-0.5');

const COLLAPSE_DELAY_MS = 180;

/**
 * The side panel's tool switcher, at the right end of the panel's top bar.
 *
 * Collapsed it is just the current tool's icon; hovering it (or keyboard
 * focus, or a tap on touch screens) drops the other tools down under it.
 * Picking another tool switches to it; picking the current one hides the
 * panel (read mode), where the switcher stays on the reader toolbar and any
 * tool brings the panel back.
 *
 * While open the order is frozen, so the icon under the pointer never moves;
 * the new current tool takes the top slot when it closes.
 */
export function PaperSidebar() {
    const [tab, setTab] = usePaperAtom(sidePanelTabAtom);
    const toggleReadMode = useSetPaperAtom(toggleReadModeAtom);
    const isReadMode = tab === 'Read';

    // Read mode shows the tool the panel will come back with.
    const [lastTool, setLastTool] = useState<SidePanelTool>('Chat');
    useEffect(() => {
        if (tab !== 'Read') setLastTool(tab);
    }, [tab]);
    const current: SidePanelTool = tab === 'Read' ? lastTool : tab;

    const [hovered, setHovered] = useState(false);
    const [keyboardFocus, setKeyboardFocus] = useState(false);
    const [pinned, setPinned] = useState(false);
    const expanded = hovered || keyboardFocus || pinned;

    const [frozenOrder, setFrozenOrder] = useState<SidePanelTool[] | null>(null);
    if (expanded && !frozenOrder) setFrozenOrder(orderFor(current));
    if (!expanded && frozenOrder) setFrozenOrder(null);
    const order = frozenOrder ?? orderFor(current);

    const rootRef = useRef<HTMLDivElement>(null);
    const lastInput = useRef<string>('mouse');
    const collapseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const cancelCollapse = () => {
        if (collapseTimer.current) clearTimeout(collapseTimer.current);
        collapseTimer.current = null;
    };
    useEffect(() => cancelCollapse, []);

    // A tap-opened switcher closes on a tap anywhere else.
    useEffect(() => {
        if (!pinned) return;
        const onPointerDown = (e: PointerEvent) => {
            if (!rootRef.current?.contains(e.target as Node)) setPinned(false);
        };
        document.addEventListener('pointerdown', onPointerDown, true);
        return () => document.removeEventListener('pointerdown', onPointerDown, true);
    }, [pinned]);

    const pick = (tool: SidePanelTool) => {
        const touch = lastInput.current === 'touch' || lastInput.current === 'pen';
        // On touch the first tap only opens the switcher.
        if (touch && !expanded) {
            setPinned(true);
            return;
        }
        if (tool === tab) toggleReadMode();
        else setTab(tool);
        if (touch) setPinned(false);
    };

    const renderTool = (tool: SidePanelTool) => {
        const { label, icon: Icon } = TOOL_META[tool];
        const active = tool === tab;
        const hint = active ? `Hide ${label.toLowerCase()} panel` : label;
        return (
            <Tooltip key={tool}>
                <TooltipTrigger asChild>
                    <button
                        type="button"
                        aria-pressed={active}
                        aria-label={hint}
                        onClick={() => pick(tool)}
                        className={cn(
                            'relative isolate flex size-7 shrink-0 items-center justify-center rounded-lg outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-inset',
                            active
                                ? 'text-brand-foreground focus-visible:ring-brand-foreground/80'
                                : 'text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring',
                        )}
                    >
                        {active && (
                            <motion.span
                                layoutId="pill"
                                transition={PILL_SPRING}
                                className="absolute inset-0 -z-10 rounded-lg bg-brand shadow-sm"
                            />
                        )}
                        <Icon className="size-4" />
                    </button>
                </TooltipTrigger>
                <TooltipContent side="left" sideOffset={8}>
                    {hint}
                </TooltipContent>
            </Tooltip>
        );
    };

    return (
        <TooltipProvider delayDuration={400}>
            <div
                ref={rootRef}
                role="toolbar"
                aria-orientation="vertical"
                aria-label="Side panel tools"
                onPointerDown={(e) => {
                    lastInput.current = e.pointerType;
                }}
                onPointerEnter={(e) => {
                    if (e.pointerType !== 'mouse') return;
                    cancelCollapse();
                    setHovered(true);
                }}
                onPointerLeave={(e) => {
                    if (e.pointerType !== 'mouse') return;
                    cancelCollapse();
                    collapseTimer.current = setTimeout(() => setHovered(false), COLLAPSE_DELAY_MS);
                }}
                onKeyDown={(e) => {
                    lastInput.current = 'keyboard';
                    if (e.key === 'Escape' && expanded) {
                        e.stopPropagation();
                        setKeyboardFocus(false);
                        setPinned(false);
                        setHovered(false);
                    }
                }}
                onFocus={(e) => {
                    if (e.target.matches(':focus-visible')) setKeyboardFocus(true);
                }}
                onBlur={(e) => {
                    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setKeyboardFocus(false);
                }}
                className={cn(
                    'absolute right-1 z-30 flex flex-col rounded-xl border p-[3px] transition-[top,background-color,border-color,box-shadow] duration-200 ease-out-soft',
                    topClass(tab),
                    expanded || isReadMode
                        ? 'border-border bg-background/95 shadow-md backdrop-blur-md dark:border-zinc-700 dark:bg-zinc-900/95 dark:shadow-black/40'
                        : 'border-transparent bg-transparent shadow-none',
                )}
            >
                <LayoutGroup id="paper-side-rail">
                    {renderTool(order[0])}
                    <div
                        className={cn(
                            'grid transition-[grid-template-rows,margin,opacity,visibility] duration-200 ease-out-soft',
                            expanded
                                ? 'visible mt-1 grid-rows-[1fr] opacity-100'
                                : 'invisible mt-0 grid-rows-[0fr] opacity-0',
                        )}
                    >
                        <div className="flex min-h-0 flex-col gap-1 overflow-hidden">
                            {order.slice(1).map(renderTool)}
                        </div>
                    </div>
                </LayoutGroup>
            </div>
        </TooltipProvider>
    );
}
