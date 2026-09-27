"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutGroup, motion } from "motion/react";
import { ChevronLeft } from "lucide-react";
import { CommandMenu } from "@/components/CommandMenu";
import { OpenPaperMark } from "@/components/OpenPaperMark";
import { UserMenu } from "@/components/UserMenu";
import { useAuth } from "@/lib/auth";
import { PILL_SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";

const NAV = [
	{ href: "/", label: "Home" },
	{ href: "/papers", label: "Library" },
	{ href: "/projects", label: "Projects" },
	{ href: "/discover", label: "Discover" },
];

const SECTIONS: [prefix: string, label: string][] = [
	["/papers", "Library"],
	["/projects", "Projects"],
	["/discover", "Discover"],
	["/settings", "Settings"],
];

function isActive(pathname: string, href: string) {
	return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The one bar of app chrome (there is no sidebar): logo, then either the
 * section nav (main pages) or a way back (`back`) plus `title` (the paper page), and
 * on the right `actions`, search (⌘K) and the account menu. Phones get the
 * section nav from the bottom tab bar instead.
 */
export function AppHeader({
	title,
	back,
	actions,
}: {
	title?: React.ReactNode;
	/** With `title`: the breadcrumb's way back (default: the library). */
	back?: React.ReactNode;
	actions?: React.ReactNode;
}) {
	const pathname = usePathname();
	const { user } = useAuth();
	const section = SECTIONS.find(([prefix]) => isActive(pathname, prefix))?.[1];

	return (
		<header className="sticky top-0 z-30 flex h-(--app-header-h) shrink-0 items-center gap-1 border-b bg-background/85 px-3 backdrop-blur-md supports-[backdrop-filter]:bg-background/70 md:gap-2 md:px-4">
			<Link
				href="/"
				aria-label="Open Paper home"
				className={cn(
					"flex shrink-0 items-center gap-2 rounded-md pr-1 transition-opacity hover:opacity-80",
					// Phones: the paper page's back chevron stands in for the logo.
					title && "max-md:hidden"
				)}
			>
				<OpenPaperMark />
				<span className={cn("text-sm font-semibold", title ? "hidden xl:inline" : "hidden md:inline")}>
					Open Paper
				</span>
			</Link>

			{title ? (
				<div className="flex min-w-0 flex-1 items-center gap-1 text-sm max-md:-ml-1.5">
					{back ?? <HeaderBackLink href="/papers" label="Library" />}
					<span aria-hidden className="hidden text-muted-foreground/50 md:inline">/</span>
					<div className="min-w-0 truncate px-1 font-medium">{title}</div>
				</div>
			) : (
				<>
					{user && (
						<nav aria-label="Main" className="hidden md:block">
							<LayoutGroup id="top-nav">
								<ul className="flex items-center gap-0.5">
									{NAV.map(({ href, label }) => {
										const active = isActive(pathname, href);
										return (
											<li key={href}>
												<Link
													href={href}
													aria-current={active ? "page" : undefined}
													className={cn(
														"relative isolate block rounded-md px-3 py-1.5 text-sm transition-colors",
														active ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
													)}
												>
													{active && (
														<motion.span
															layoutId="pill"
															transition={PILL_SPRING}
															className="absolute inset-0 -z-10 rounded-md bg-accent"
														/>
													)}
													{label}
												</Link>
											</li>
										);
									})}
								</ul>
							</LayoutGroup>
						</nav>
					)}
					{/* Phones: the tab bar navigates; the header just says where you are. */}
					<div className="min-w-0 flex-1 truncate pl-1 text-sm font-semibold md:hidden">
						{section ?? "Open Paper"}
					</div>
					<div className="hidden flex-1 md:block" />
				</>
			)}

			{actions}
			{/* Home has its own search box (which owns ⌘K there). */}
			{pathname !== "/" && <CommandMenu compact={!!title} className={title ? "max-md:hidden" : undefined} />}
			<UserMenu />
		</header>
	);
}

/** The breadcrumb's "‹ Parent" link; just the chevron on phones. */
export function HeaderBackLink({ href, label }: { href: string; label: string }) {
	return (
		<Link
			href={href}
			title={label}
			aria-label={`Back to ${label}`}
			className="flex size-9 min-w-0 shrink-0 items-center justify-center gap-0.5 rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground md:size-auto md:max-w-[16rem] md:shrink md:justify-start md:px-1.5 md:py-1 lg:shrink-0"
		>
			<ChevronLeft className="size-5 shrink-0 md:size-4" />
			<span className="hidden truncate md:inline">{label}</span>
		</Link>
	);
}
