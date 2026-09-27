import { AppSidebar } from "@/components/AppSidebar";
import { AppHeader } from "@/components/AppHeader";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { SidebarController } from "@/components/utils/SidebarAutoCollapse";
import { ManageProjectsButton } from "@/components/ManageProjectsButton";
import { CitePaperButton } from "@/components/CitePaperButton";
import { PaperStoreProvider } from "@/components/paper/PaperStoreProvider";
import { PaperHeaderTitle } from "@/components/paper/PaperHeaderTitle";
import { HeaderPaperStatusButton } from "@/components/HeaderPaperStatusButton";
import { IngestFailureButton } from "@/components/ingest/IngestStatus";

/**
 * The reader is an app-like screen: the inset is exactly one viewport tall
 * and the panes inside own their scrolling (the document never scrolls).
 */
export default function PaperLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<SidebarProvider>
			<AppSidebar />
			<SidebarInset className="h-dvh overflow-hidden">
				<PaperStoreProvider>
					<AppHeader
						title={<PaperHeaderTitle />}
						actions={
							<div className="flex shrink-0 items-center gap-0.5 md:gap-1">
								<IngestFailureButton />
								<ManageProjectsButton />
								<HeaderPaperStatusButton />
								<CitePaperButton collapseLabel />
							</div>
						}
					/>
					<div className="flex min-h-0 flex-1 flex-col">
						<SidebarController>
							{children}
						</SidebarController>
					</div>
				</PaperStoreProvider>
			</SidebarInset>
		</SidebarProvider>
	);
}
