import { notFound } from "next/navigation";
import Harness from "./Harness";

export default function CitationBenchmarkPage() {
  if (process.env.NODE_ENV === "production" || process.env.CITATION_BENCHMARK !== "1") notFound();
  return <Harness />;
}
