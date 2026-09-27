"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isChunkLoadError, reloadForStaleDeploy } from "@/lib/staleDeploy";

export default function ErrorPage({
	error,
	reset,
}: {
	error: Error & { digest?: string };
	reset: () => void;
}) {
	const staleDeploy = isChunkLoadError(error);

	useEffect(() => {
		console.error(error);
		if (staleDeploy) reloadForStaleDeploy();
	}, [error, staleDeploy]);

	return (
		<div className="flex min-h-svh items-center justify-center p-4">
			<Card className="w-full max-w-md">
				<CardHeader className="text-center">
					<div className="mx-auto mb-2 rounded-full bg-destructive/10 p-3 text-destructive">
						<AlertCircle className="h-6 w-6" />
					</div>
					<CardTitle className="text-xl">Something went wrong</CardTitle>
					<CardDescription className="break-words">
						{error.message || "An unexpected error occurred."}
					</CardDescription>
				</CardHeader>
				<CardContent className="flex justify-center gap-2">
					<Button onClick={() => (staleDeploy ? window.location.reload() : reset())}>Try again</Button>
					<Button variant="outline" asChild>
						<Link href="/">Go home</Link>
					</Button>
				</CardContent>
			</Card>
		</div>
	);
}
