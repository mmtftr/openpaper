"use client";

// Replaces the root layout when the root layout itself throws, so it renders
// its own <html>/<body> and can't rely on the root layout's providers.
import "./globals.css";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { isChunkLoadError, reloadForStaleDeploy } from "@/lib/staleDeploy";

export default function GlobalError({
	error,
	reset,
}: {
	error: Error & { digest?: string };
	reset: () => void;
}) {
	const staleDeploy = isChunkLoadError(error);

	useEffect(() => {
		if (staleDeploy) reloadForStaleDeploy();
	}, [staleDeploy]);

	return (
		<html lang="en">
			<body className="antialiased">
				<div className="flex min-h-svh flex-col items-center justify-center gap-4 p-4 text-center">
					<h1 className="text-xl font-semibold">Something went wrong</h1>
					<p className="max-w-md break-words text-sm text-muted-foreground">
						{error.message || "An unexpected error occurred."}
					</p>
					<Button onClick={() => (staleDeploy ? window.location.reload() : reset())}>Try again</Button>
				</div>
			</body>
		</html>
	);
}
