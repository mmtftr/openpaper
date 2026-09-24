import { AppSidebar } from "@/components/AppSidebar";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { SidebarController } from "@/components/utils/SidebarAutoCollapse";
import Image from "next/image";
import Link from "next/link";

export default function MainLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<SidebarProvider>
			<AppSidebar />
			<SidebarInset>
				<header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
					<SidebarTrigger className="-ml-1" />
					<Separator orientation="vertical" className="mr-2 h-4" />
					<Link href="/" className="flex flex-1 items-center gap-2 hover:opacity-80 transition-opacity">
						<Image
							src="/openpaper.svg"
							width={24}
							height={24}
							alt="Open Paper Logo"
						/>
						<span className="text-sm font-semibold">Open Paper</span>
					</Link>
				</header>
				<SidebarController>
					{children}
				</SidebarController>
			</SidebarInset>
		</SidebarProvider>
	);
}
