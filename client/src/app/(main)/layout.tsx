import { AppSidebar } from "@/components/AppSidebar";
import { AppHeader } from "@/components/AppHeader";
import { MobileTabBar } from "@/components/MobileTabBar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { SidebarController } from "@/components/utils/SidebarAutoCollapse";

export default function MainLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<SidebarProvider>
			<AppSidebar />
			<SidebarInset>
				<AppHeader />
				<SidebarController>
					{/* Clears the phone tab bar; pages scroll the document. */}
					<div className="flex flex-1 flex-col pb-tabbar md:pb-0">{children}</div>
				</SidebarController>
				<MobileTabBar />
			</SidebarInset>
		</SidebarProvider>
	);
}
