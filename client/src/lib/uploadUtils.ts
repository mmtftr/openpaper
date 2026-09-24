import { api, unwrap } from "@/lib/api/client"
import { MinimalJob } from "@/lib/schema"

export const MAX_UPLOAD_SIZE_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB) || 50;

/**
 * Fetches a PDF from a URL client-side and returns it as a File object.
 * Extracts filename from content-disposition header or URL path,
 * falling back to a random filename if needed.
 */
const fetchPdfAsFile = async (url: string): Promise<File> => {
    const response = await fetch(url, {
        method: 'GET',
    });

    if (!response.ok) throw new Error('Failed to fetch PDF');

    const contentDisposition = response.headers.get('content-disposition');
    const randomFilename = Math.random().toString(36).substring(2, 15) + '.pdf';
    let filename = randomFilename;

    if (contentDisposition && contentDisposition.includes('attachment')) {
        const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
        const matches = filenameRegex.exec(contentDisposition);
        if (matches != null && matches[1]) {
            filename = matches[1].replace(/['"]/g, '');
        }
    } else {
        const urlParts = url.split('/');
        const urlFilename = urlParts[urlParts.length - 1];
        if (urlFilename && urlFilename.toLowerCase().endsWith('.pdf')) {
            filename = urlFilename;
        }
    }

    const blob = await response.blob();
    return new File([blob], filename, { type: 'application/pdf' });
}

/**
 * `POST /api/paper/upload` as multipart. The generated body type describes the
 * file as a (binary) string; the `File` goes through as-is and the serializer
 * wraps it in the FormData the endpoint expects.
 */
const postUpload = async (
    file: File,
    query: { project_id?: string; supplementary_of?: string },
): Promise<MinimalJob> => {
    const res = await unwrap(api.POST("/api/paper/upload", {
        params: { query },
        body: { file: file as unknown as string },
        bodySerializer: ({ file }) => {
            const formData = new FormData()
            formData.append("file", file)
            return formData
        },
    }))
    return { jobId: res.job_id, fileName: file.name }
}

/**
 * Uploads a single file, optionally associating it with a project.
 */
const uploadFile = (file: File, projectId?: string): Promise<MinimalJob> =>
    postUpload(file, { project_id: projectId })

/**
 * Uploads a single PDF as a supplementary material attached to a parent paper.
 */
export const uploadSupplementaryFile = (parentPaperId: string, file: File): Promise<MinimalJob> =>
    postUpload(file, { supplementary_of: parentPaperId })

export const uploadFiles = async (files: File[]): Promise<MinimalJob[]> => {
    const newJobs: MinimalJob[] = []
    const errors: Error[] = []
    for (const file of files) {
        try {
            const job = await uploadFile(file)
            newJobs.push(job)
        } catch (error) {
            console.error("Failed to start upload for", file.name, error)
            errors.push(error instanceof Error ? error : new Error(String(error)))
        }
    }
    // If all uploads failed, throw the first error so the caller knows what went wrong
    if (newJobs.length === 0 && errors.length > 0) {
        throw errors[0]
    }
    return newJobs
}

export const uploadFromUrl = async (url: string, projectId?: string): Promise<MinimalJob> => {
    const res = await unwrap(api.POST("/api/paper/upload/from-url", {
        params: { query: { project_id: projectId } },
        body: { url },
    }))
    return { jobId: res.job_id, fileName: url }
}

/**
 * Uploads a PDF from a URL, first attempting client-side fetch for better filename handling,
 * then falling back to server-side fetch if that fails.
 */
export const uploadFromUrlWithFallback = async (url: string, projectId?: string): Promise<MinimalJob> => {
    try {
        const file = await fetchPdfAsFile(url);
        return await uploadFile(file, projectId);
    } catch (error) {
        console.log('Client-side fetch failed, trying server-side fetch...', error);
        return await uploadFromUrl(url, projectId);
    }
}

// Convenience alias for project uploads
export const uploadFromUrlWithFallbackForProject = (url: string, projectId: string): Promise<MinimalJob> => {
    return uploadFromUrlWithFallback(url, projectId);
}
