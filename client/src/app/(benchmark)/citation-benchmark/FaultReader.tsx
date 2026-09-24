"use client";

import { useEffect, useState } from "react";
import { Provider, useAtomValue } from "jotai";
import PdfPane from "@/components/reader/PdfPane";
import { pdfDocAtom } from "@/components/reader/atoms";

/** Fault injection on the real document, confined to the gated dev harness. */
function TextFailure({ fault }: { fault: string }) {
  const doc = useAtomValue(pdfDocAtom);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!doc) return;
    const original = doc.getPage;
    doc.getPage = async number => {
      const page = await original.call(doc, number);
      return new Proxy(page, {
        get(target, key) {
          if (key === "getTextContent") return async () => {
            if (fault === "throw") throw new Error("Benchmark text extraction failure");
            return { items: [], styles: {} };
          };
          const value = Reflect.get(target, key, target);
          return typeof value === "function" ? value.bind(target) : value;
        },
      });
    };
    setReady(true);
    return () => { doc.getPage = original; };
  }, [doc, fault]);
  return ready ? <span hidden data-citation-fault-ready={fault} /> : null;
}

export default function FaultReader({ pdfUrl, fault }: { pdfUrl: string; fault: string }) {
  return <Provider><PdfPane pdfUrl={pdfUrl} displayedPaperId="">
    <TextFailure fault={fault} />
  </PdfPane></Provider>;
}
