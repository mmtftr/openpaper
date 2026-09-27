"use client"

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutGroup, motion } from "motion/react";
import { PILL_SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";

const TABS = [
	{ href: "/settings", label: "Profile" },
	{ href: "/settings/models", label: "Models" },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
	const pathname = usePathname();
	return (
		<div className="w-full max-w-3xl">
			<div className="space-y-3 px-4 pt-5 sm:px-6 sm:pt-8">
				<h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
				<LayoutGroup id="settings-tabs">
					<nav aria-label="Settings" className="flex gap-1 border-b">
						{TABS.map((tab) => {
							const active = pathname === tab.href;
							return (
								<Link
									key={tab.href}
									href={tab.href}
									aria-current={active ? "page" : undefined}
									className={cn(
										"relative flex h-10 items-center px-2.5 text-sm transition-colors",
										active ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
									)}
								>
									{tab.label}
									{active && (
										<motion.span
											layoutId="underline"
											transition={PILL_SPRING}
											className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-foreground"
										/>
									)}
								</Link>
							);
						})}
					</nav>
				</LayoutGroup>
			</div>
			{children}
		</div>
	);
}
