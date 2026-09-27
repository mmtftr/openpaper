"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutGroup, motion } from "motion/react";
import { Compass, FileText, FolderKanban, Home } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { PILL_SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";

const TABS = [
	{ href: "/", label: "Home", icon: Home },
	{ href: "/papers", label: "Library", icon: FileText },
	{ href: "/projects", label: "Projects", icon: FolderKanban },
	{ href: "/discover", label: "Discover", icon: Compass },
];

function isActive(pathname: string, href: string) {
	return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Bottom navigation for the main pages on phones (hidden from `md` up, where
 * the sidebar does the job). Pages clear it with `pb-tabbar`.
 */
export function MobileTabBar() {
	const pathname = usePathname();
	const { user } = useAuth();
	if (!user) return null;

	return (
		<nav
			aria-label="Main"
			className="fixed inset-x-0 bottom-0 z-40 border-t pr-[env(safe-area-inset-right)] pl-[env(safe-area-inset-left)] bg-background/90 pb-safe backdrop-blur-md supports-[backdrop-filter]:bg-background/75 md:hidden"
		>
			<LayoutGroup id="main-tabbar">
				<ul className="mx-auto flex h-(--app-tabbar-h) max-w-md items-stretch justify-around px-2">
					{TABS.map(({ href, label, icon: Icon }) => {
						const active = isActive(pathname, href);
						return (
							<li key={href} className="flex flex-1">
								<Link
									href={href}
									aria-current={active ? "page" : undefined}
									className={cn(
										"relative isolate flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors",
										active ? "text-foreground" : "text-muted-foreground"
									)}
								>
									{active && (
										<motion.span
											layoutId="pill"
											transition={PILL_SPRING}
											className="absolute inset-x-2 inset-y-1.5 -z-10 rounded-xl bg-accent"
										/>
									)}
									<Icon className={cn("size-5 transition-transform duration-200 ease-out-soft", active && "scale-105")} strokeWidth={active ? 2.2 : 1.8} />
									{label}
								</Link>
							</li>
						);
					})}
				</ul>
			</LayoutGroup>
		</nav>
	);
}
