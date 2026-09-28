import { NextRequest, NextResponse } from "next/server";

// Name of the cookie the API sets on login (server/app/auth/dependencies.py).
const SESSION_COOKIE = "session_token";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

// Pages reachable without a session. Everything else under the matcher below
// redirects to /login when the session cookie is missing.
const PUBLIC_PREFIXES = ["/login", "/auth/", "/reader-benchmark", "/citation-benchmark"];

function isPublic(pathname: string) {
	return PUBLIC_PREFIXES.some(prefix => pathname === prefix || pathname.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`));
}

/**
 * The API's session cookie is only visible here when the browser sends it to
 * this host too. Cookies are scoped by host (not port), so that holds when the
 * API is same-origin (production: Caddy serves client and API on one origin) or on another port
 * of the same host (local dev: localhost:8002 -> localhost:8003). If the API
 * lives on a different host we can't see the cookie, so the middleware steps
 * aside and the pages' own client-side checks handle it.
 *
 * The request host comes from the (forwarded) Host header, not
 * `request.nextUrl`: under `next start` the latter is the server's bind
 * address (e.g. localhost:3000), not what the browser asked Caddy for.
 */
function canSeeSessionCookie(request: NextRequest) {
	if (!API_URL) return true; // relative API URLs = same origin
	const host = publicHost(request);
	if (!host) return false;
	try {
		return new URL(`http://${host}`).hostname === new URL(API_URL).hostname;
	} catch {
		return false;
	}
}

function firstHeader(request: NextRequest, name: string) {
	return request.headers.get(name)?.split(",")[0].trim() || null;
}

function publicHost(request: NextRequest) {
	return firstHeader(request, "x-forwarded-host") ?? firstHeader(request, "host");
}

export function middleware(request: NextRequest) {
	const { pathname, search } = request.nextUrl;
	if (isPublic(pathname)) return NextResponse.next();
	if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
	if (!canSeeSessionCookie(request)) return NextResponse.next();

	// Build the redirect against the public origin so the Location header
	// doesn't point at the server's internal bind address.
	const proto = firstHeader(request, "x-forwarded-proto") ?? request.nextUrl.protocol.replace(/:$/, "");
	const loginUrl = new URL("/login", `${proto}://${publicHost(request)}`);
	// Same param the login page reads (`?returnTo=`) to send the user back.
	if (pathname !== "/") loginUrl.searchParams.set("returnTo", `${pathname}${search}`);
	return NextResponse.redirect(loginUrl);
}

export const config = {
	// Skip Next internals, API routes and anything that looks like a file
	// (public/ assets, icon.svg, pdf.js worker/cmaps, ...).
	matcher: ["/((?!_next/|__nextjs|api/|.*\\.[^/]+$).*)"],
};
