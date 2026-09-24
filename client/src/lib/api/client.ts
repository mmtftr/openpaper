/**
 * Typed API client generated from the server's OpenAPI schema.
 *
 *   const papers = await unwrap(api.GET("/api/paper/all"));
 *   const { data } = useSWR(["/api/paper/outline", id], () =>
 *       unwrap(api.GET("/api/paper/outline", { params: { query: { id } } })));
 *
 * Regenerate the types after changing server routes: `yarn gen:api`
 * (a server test fails while `openapi.json` is stale).
 */
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

export const api = createClient<paths>({ baseUrl: API_BASE_URL, credentials: "include" });

export type Schemas = components["schemas"];

/** A non-2xx API response. `message` is the server's `detail`. */
export class ApiRequestError extends Error {
    constructor(
        message: string,
        readonly status: number,
        readonly body: unknown,
    ) {
        super(message);
        this.name = "ApiRequestError";
    }
}

/** The server's error text: `{detail: string}`, or FastAPI's 422 `{detail: [{msg}]}`. */
export function errorDetail(body: unknown, status: number): string {
    const detail = (body as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
        return detail.map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d)).join("; ");
    }
    return `API error: ${status}`;
}

/** Resolve an openapi-fetch call to its data, throwing `ApiRequestError` on failure. */
export async function unwrap<T>(
    call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
    const { data, error, response } = await call;
    if (!response.ok) {
        throw new ApiRequestError(errorDetail(error, response.status), response.status, error);
    }
    return data as T;
}
