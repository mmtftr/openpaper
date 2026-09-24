import { notFound } from "next/navigation";
import Harness from "./Harness";

export default function BenchmarkPage() {
	if (process.env.NODE_ENV === "production" || process.env.ANNOTATION_BENCHMARK !== "1") notFound();
	return <Harness />;
}
