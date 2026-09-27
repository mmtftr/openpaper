import { AppHeader } from "@/components/AppHeader";
import { MobileTabBar } from "@/components/MobileTabBar";

export default function MainLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<div className="flex min-h-dvh flex-col">
			<AppHeader />
			{/* Clears the phone tab bar; pages scroll the document. */}
			<main className="flex flex-1 flex-col pb-tabbar md:pb-0">{children}</main>
			<MobileTabBar />
		</div>
	);
}
