"use client"

import {
    SidebarMenuItem,
    SidebarMenuButton,
    SidebarMenuSub,
    SidebarMenuSubButton,
    SidebarMenuSubItem,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./ui/collapsible";
import Link from "next/link";
import { ArrowRight, ChevronDown } from "lucide-react";
import React from "react";

interface CollapsibleSidebarMenuProps<T extends { id: string; title?: string | null; }> {
    icon: React.ElementType;
    title: string;
    url: string;
    items: T[];
    viewAllUrl: string;
    viewAllText: string;
    getItemUrl: (item: T) => string;
    getItemName?: (item: T) => string | null | undefined;
    defaultOpen?: boolean;
    maxItems?: number;
    tag?: string;
    /** The current route, to mark the section and the open item as active. */
    pathname?: string;
}

export function CollapsibleSidebarMenu<T extends { id: string; title?: string | null; }>({
    icon: Icon,
    title,
    url,
    items,
    viewAllUrl,
    viewAllText,
    getItemUrl,
    getItemName,
    defaultOpen = false,
    maxItems = 7,
    tag,
    pathname = "",
}: CollapsibleSidebarMenuProps<T>) {
    const sectionActive = pathname === url;
    return (
        <Collapsible asChild defaultOpen={defaultOpen} className="group/collapsible">
            <SidebarMenuItem>
                <div className="flex items-center w-full gap-0.5">
                    <SidebarMenuButton asChild isActive={sectionActive} className="flex-1">
                        <Link href={url} aria-current={sectionActive ? "page" : undefined}>
                            <Icon />
                            <span>{title}</span>
                            {tag && (
                                <span className="ml-1 text-xs text-yellow-500 bg-yellow-100 dark:bg-yellow-800 dark:text-yellow-200 px-1 rounded">
                                    {tag}
                                </span>
                            )}
                        </Link>
                    </SidebarMenuButton>
                    <CollapsibleTrigger asChild>
                        <button
                            className="flex size-7 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
                            aria-label={`Toggle ${title}`}
                        >
                            <ChevronDown className="h-4 w-4 transition-transform duration-200 ease-out-soft group-data-[state=open]/collapsible:rotate-180" />
                        </button>
                    </CollapsibleTrigger>
                </div>
                <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down ease-out-soft">
                    <SidebarMenuSub>
                        {items.slice(0, maxItems).map((item) => {
                            const href = getItemUrl(item);
                            const name = (getItemName ? getItemName(item) : item.title) || "Untitled";
                            const active = pathname === href;
                            return (
                                <SidebarMenuSubItem key={item.id}>
                                    <SidebarMenuSubButton asChild isActive={active}>
                                        <Link
                                            href={href}
                                            title={name}
                                            aria-current={active ? "page" : undefined}
                                            className="h-fit py-1.5 text-xs text-sidebar-foreground/80 data-[active=true]:text-sidebar-accent-foreground"
                                        >
                                            <p className="line-clamp-2 leading-snug">{name}</p>
                                        </Link>
                                    </SidebarMenuSubButton>
                                </SidebarMenuSubItem>
                            );
                        })}
                        {items.length > maxItems && (
                            <SidebarMenuSubItem>
                                <SidebarMenuSubButton asChild>
                                    <Link href={viewAllUrl} className="group/viewall h-fit py-1.5 text-xs text-muted-foreground">
                                        {viewAllText}
                                        <ArrowRight className="inline h-3 w-3 transition-transform duration-200 ease-out-soft group-hover/viewall:translate-x-0.5" />
                                    </Link>
                                </SidebarMenuSubButton>
                            </SidebarMenuSubItem>
                        )}
                    </SidebarMenuSub>
                </CollapsibleContent>
            </SidebarMenuItem>
        </Collapsible>
    );
}
