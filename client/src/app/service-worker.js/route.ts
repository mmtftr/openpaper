import { killServiceWorkerResponse } from "@/lib/killServiceWorker";

// Self-destructing replacement for a service worker once registered here.
export const dynamic = "force-dynamic";

export function GET() {
	return killServiceWorkerResponse();
}
