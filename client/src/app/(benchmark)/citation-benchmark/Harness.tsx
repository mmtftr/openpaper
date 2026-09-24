"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

const Reader = dynamic(() => import("@/components/reader/PdfReader").then(m => m.PdfReader), { ssr: false });
const FaultReader = dynamic(() => import("./FaultReader"), { ssr: false });

export default function Harness() {
  const [paper, setPaper] = useState("");
  const [fault, setFault] = useState("");
  useEffect(() => {
    const query = new URLSearchParams(location.search);
    setPaper(query.get("paper") ?? "");
    setFault(query.get("reviewFault") ?? "");
  }, []);
  return <main style={{ height: "100vh", width: "100vw" }}>
    {paper && (fault
      ? <FaultReader pdfUrl={`/citation-benchmark/assets/${paper}.pdf`} fault={fault} />
      : <Reader pdfUrl={`/citation-benchmark/assets/${paper}.pdf`} displayedPaperId={paper} />)}
  </main>;
}
