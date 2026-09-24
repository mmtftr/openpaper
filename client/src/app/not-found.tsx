import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
	return (
		<div className="flex min-h-svh flex-col items-center justify-center gap-4 p-4 text-center">
			<h1 className="text-2xl font-semibold">Page not found</h1>
			<p className="text-sm text-muted-foreground">
				The page you&apos;re looking for doesn&apos;t exist.
			</p>
			<Button asChild>
				<Link href="/">Go home</Link>
			</Button>
		</div>
	);
}
