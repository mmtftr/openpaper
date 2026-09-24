import { notFound } from "next/navigation";
import "../globals.css";

export default function BenchmarkLayout({ children }: { children: React.ReactNode }) {
	if (process.env.NODE_ENV === "production" || (process.env.ANNOTATION_BENCHMARK !== "1" && process.env.CITATION_BENCHMARK !== "1")) notFound();
	return <html lang="en"><body>{children}</body></html>;
}
