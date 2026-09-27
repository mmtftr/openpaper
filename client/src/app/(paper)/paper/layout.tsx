import { AppHeader } from "@/components/AppHeader";
import { PaperStoreProvider } from "@/components/paper/PaperStoreProvider";
import { PaperHeaderTitle } from "@/components/paper/PaperHeaderTitle";
import { PaperBreadcrumb } from "@/components/paper/PaperBreadcrumb";
import { PaperInfoMenu } from "@/components/paper/PaperInfoMenu";
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
		<div className="flex h-dvh flex-col overflow-hidden">
			<PaperStoreProvider>
				<AppHeader
					title={<PaperHeaderTitle />}
					back={<PaperBreadcrumb />}
					actions={
						<div className="flex shrink-0 items-center gap-0.5 md:gap-1">
							<IngestFailureButton />
							<PaperInfoMenu />
						</div>
					}
				/>
				<main className="flex min-h-0 flex-1 flex-col">{children}</main>
			</PaperStoreProvider>
		</div>
	);
}
