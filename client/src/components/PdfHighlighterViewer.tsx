import dynamic from "next/dynamic";

import type {
	PdfHighlighterViewerProps,
	RenderedHighlightPosition,
	ExtendedHighlight,
} from "./PdfHighlighterViewerImpl";

const PdfHighlighterViewerImpl = dynamic(
	() =>
		import("./PdfHighlighterViewerImpl").then(
			(module) => module.PdfHighlighterViewer
		),
	{
		ssr: false,
	}
);

export function PdfHighlighterViewer(props: PdfHighlighterViewerProps) {
	return <PdfHighlighterViewerImpl {...props} />;
}

export type {
	PdfHighlighterViewerProps,
	RenderedHighlightPosition,
	ExtendedHighlight,
};
