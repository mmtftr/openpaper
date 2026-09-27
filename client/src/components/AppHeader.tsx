"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";

/** Section names for the header breadcrumb, by route prefix. */
const SECTIONS: [prefix: string, label: string][] = [
	["/papers", "Library"],
	["/projects", "Projects"],
	["/discover", "Discover"],
	["/settings", "Settings"],
];

function sectionFor(pathname: string): string | null {
	return SECTIONS.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`))?.[1] ?? null;
}

/**
 * The bar above every page: sidebar toggle, logo, then where you are — the
 * section on main pages, or whatever the layout passes as `title` (the
 * paper's title on the reader). `actions` sit on the right.
 */
export function AppHeader({ title, actions }: { title?: React.ReactNode; actions?: React.ReactNode }) {
	const pathname = usePathname();
	const crumb = title ?? sectionFor(pathname);

	return (
		<header className="sticky top-0 z-30 flex h-(--app-header-h) shrink-0 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur-md supports-[backdrop-filter]:bg-background/70">
			<SidebarTrigger className="size-8" />
			<Separator orientation="vertical" className="mx-1 data-[orientation=vertical]:h-4" />
			<div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
				<Link
					href="/"
					className="flex shrink-0 items-center gap-2 rounded-md transition-opacity hover:opacity-80"
					aria-label="Open Paper home"
				>
					<Image src="/openpaper.svg" width={22} height={22} alt="" />
					<span className={crumb ? "hidden font-semibold sm:inline" : "font-semibold"}>Open Paper</span>
				</Link>
				{crumb && (
					<>
						<span aria-hidden className="hidden text-muted-foreground/60 sm:inline">/</span>
						<div className="min-w-0 truncate font-medium text-foreground/90">{crumb}</div>
					</>
				)}
			</div>
			{actions}
		</header>
	);
}
