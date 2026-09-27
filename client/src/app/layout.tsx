import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider } from "next-themes";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { Toaster } from "@/components/ui/sonner";
import { MotionProvider } from "@/components/utils/MotionProvider";

const geistSans = Geist({
	variable: "--font-geist-sans",
	subsets: ["latin"],
});

const geistMono = Geist_Mono({
	variable: "--font-geist-mono",
	subsets: ["latin"],
});

export const metadata: Metadata = {
	title: "Open Paper",
	description: "The fastest way to annotate and deeply understand research papers.",
	icons: {
		icon: "/icon.svg"
	},
	openGraph: {
		title: "Open Paper",
		description: "The fastest way to annotate and deeply understand research papers.",
		images: [
			{
				url: "https://assets.khoj.dev/openpaper/hero_open_paper2.png",
				width: 1280,
				height: 640,
				alt: "Open Paper",
			}
		],
		type: "website",
	},
	twitter: {
		card: "summary_large_image",
		title: "Open Paper",
		description: "The fastest way to annotate and deeply understand your research papers.",
		images: ["https://assets.khoj.dev/openpaper/hero_open_paper2.png"],
	},
};

// `viewport-fit=cover` exposes the safe-area insets the bottom bars pad for.
export const viewport: Viewport = {
	width: "device-width",
	initialScale: 1,
	viewportFit: "cover",
	themeColor: [
		{ media: "(prefers-color-scheme: light)", color: "#ffffff" },
		{ media: "(prefers-color-scheme: dark)", color: "#1c1f22" },
	],
};

export default function RootLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<html lang="en" suppressHydrationWarning>
			<body
				className={`${geistSans.variable} ${geistMono.variable} antialiased`}
			>
				{/*
				 * `storageKey="darkMode"` keeps the key (and its "dark"/"light"
				 * values) the old hand-rolled toggle wrote, so saved preferences
				 * carry over. No saved preference = follow the system.
				 */}
				<ThemeProvider
					attribute="class"
					defaultTheme="system"
					enableSystem
					enableColorScheme={false}
					storageKey="darkMode"
				>
					<MotionProvider>
						<AuthProvider>
							{children}
						</AuthProvider>
					</MotionProvider>
					<Toaster
						position="top-right"
						richColors
						duration={3000}
					/>
				</ThemeProvider>
			</body>
		</html>
	);
}
