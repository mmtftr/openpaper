import { readFile } from "node:fs/promises";
import path from "node:path";

export async function GET(_request: Request, { params }: { params: Promise<{ file: string }> }) {
  if (process.env.NODE_ENV === "production" || process.env.CITATION_BENCHMARK !== "1") return new Response(null, { status: 404 });
  const { file } = await params;
  if (!/^[a-f0-9-]{36}\.pdf$/.test(file)) return new Response(null, { status: 404 });
  try {
    const bytes = await readFile(path.resolve(process.cwd(), "../benchmarks/citation-hover/.data/pdfs", file));
    return new Response(bytes, { headers: { "Content-Type": "application/pdf", "Cache-Control": "no-store" } });
  } catch {
    return new Response(null, { status: 404 });
  }
}
