/**
 * Legacy untyped fetch helpers, kept only for pages / components not yet moved
 * to the typed client. New code: `api` + `unwrap` from `@/lib/api/client`.
 */
import { API_BASE_URL } from "@/lib/api/client";

export { API_BASE_URL };

/** @deprecated Use `unwrap(api.GET|POST|...(...))` from `@/lib/api/client`. */
export async function fetchFromApi(endpoint: string, options: RequestInit = {}) {
    const headers: HeadersInit = {};

    // Only set Content-Type to application/json if we're not sending FormData
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers: {
            ...headers,
            ...options.headers,
        },
        credentials: 'include', // Include cookies for auth
    });

    if (!response.ok) {
        let errorMessage: unknown = `API error: ${response.status}`;

        try {
            const errorData = await response.json();
            if (errorData.message) {
                errorMessage = errorData.message;
            } else if (errorData.error) {
                errorMessage = errorData.error;
            } else if (errorData.detail) {
                errorMessage = errorData.detail;
            }
        } catch {
            // If we can't parse the error response, fall back to status text
            errorMessage = `API error: ${response.status} ${response.statusText}`;
        }

        if (typeof errorMessage !== 'string') {
            errorMessage = JSON.stringify(errorMessage);
        }

        throw new Error(errorMessage as string)
    }

    if (response.status === 204) {
        return null; // No content to return
    }

    return response.json();
}

/** @deprecated Stream with raw `fetch` against `API_BASE_URL` from `@/lib/api/client`. */
export async function fetchStreamFromApi(
    endpoint: string,
    options: RequestInit = {}
): Promise<ReadableStream<Uint8Array>> {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers: {
            ...options.headers,
            // For SSE, we want text/event-stream instead of octet-stream
            Accept: 'text/event-stream',
        },
        credentials: 'include', // Include cookies for auth
    });

    if (!response.ok) {
        let errorMessage: unknown = `API error: ${response.status}`;

        try {
            const errorData = await response.json();
            if (errorData.message) {
                errorMessage = errorData.message;
            } else if (errorData.error) {
                errorMessage = errorData.error;
            } else if (errorData.detail) {
                errorMessage = errorData.detail;
            }
        } catch {
            // If we can't parse the error response, fall back to status text
            errorMessage = `API error: ${response.status} ${response.statusText}`;
        }

        if (typeof errorMessage !== 'string') {
            errorMessage = JSON.stringify(errorMessage);
        }

        throw new Error(errorMessage as string);
    }

    if (!response.body) {
        throw new Error('Response body is null');
    }

    return response.body;
}

/** @deprecated Use `api.GET("/api/projects/papers/from/{paper_id}")`. */
export async function getProjectsForPaper(paperId: string) {
    return fetchFromApi(`/api/projects/papers/from/${paperId}`);
}
