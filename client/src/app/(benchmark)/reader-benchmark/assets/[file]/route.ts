import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export async function GET(_request: Request, { params }: { params: Promise<{ file: string }> }) {
	if (process.env.NODE_ENV === "production" || process.env.ANNOTATION_BENCHMARK !== "1") {
		return new Response(null, { status: 404 });
	}
	const { file } = await params;
	if (file !== "manifest.json" && !/^[0-9a-f-]{36}\.pdf$/.test(file)) {
		return new Response(null, { status: 404 });
	}
	try {
		const directory = process.env.ANNOTATION_BENCHMARK_FIXTURES ?? resolve(process.cwd(), "../benchmarks/annotation-jump/.data/fixtures");
		const data = await readFile(resolve(directory, file));
		return new Response(data, { headers: {
			"Content-Type": file.endsWith(".pdf") ? "application/pdf" : "application/json",
			"Cache-Control": "no-store",
		} });
	} catch {
		return new Response(null, { status: 404 });
	}
}
