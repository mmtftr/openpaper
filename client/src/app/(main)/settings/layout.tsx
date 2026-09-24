"use client"

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS = [
	{ href: "/settings", label: "Profile" },
	{ href: "/settings/models", label: "Models" },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
	const pathname = usePathname();
	return (
		<div>
			<div className="max-w-3xl px-6 pt-6 space-y-4">
				<h1 className="text-2xl font-bold">Settings</h1>
				<nav className="flex gap-4 border-b">
					{TABS.map((tab) => (
						<Link
							key={tab.href}
							href={tab.href}
							className={cn(
								"-mb-px border-b-2 px-1 pb-2 text-sm",
								pathname === tab.href
									? "border-primary font-medium text-foreground"
									: "border-transparent text-muted-foreground hover:text-foreground"
							)}
						>
							{tab.label}
						</Link>
					))}
				</nav>
			</div>
			{children}
		</div>
	);
}
