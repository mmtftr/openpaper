import { AppSidebar } from "@/components/AppSidebar";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { SidebarController } from "@/components/utils/SidebarAutoCollapse";
import Image from "next/image";
import Link from "next/link";
import { ManageProjectsButton } from "@/components/ManageProjectsButton";
import { MobilePaperMenu } from "@/components/MobilePaperMenu";
import { CitePaperButton } from "@/components/CitePaperButton";
import { PaperStoreProvider } from "@/components/paper/PaperStoreProvider";
import { HeaderPaperStatusButton } from "@/components/HeaderPaperStatusButton";
import { IngestStatusPopover } from "@/components/ingest/IngestStatusPopover";

export default function PaperLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<SidebarProvider>
			<AppSidebar />
			<SidebarInset>
				<PaperStoreProvider>
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
						{/* Desktop buttons */}
						<div className="hidden md:flex items-center gap-2">
							<IngestStatusPopover />
							<ManageProjectsButton />
							<HeaderPaperStatusButton />
							<CitePaperButton />
						</div>
						{/* Mobile menu */}
						<MobilePaperMenu />
					</header>
					<SidebarController>
						{children}
					</SidebarController>
				</PaperStoreProvider>
			</SidebarInset>
		</SidebarProvider>
	);
}
