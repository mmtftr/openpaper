export type { ExtendedHighlight } from "./types";
export { paperHighlightToExtended, extendedToPaperHighlight } from "./types";
export {
	normalizeForSearch,
	expandLatexCommands,
	normalizeForPdfMatch,
	normalizePdfTextForMatch,
	findServerAlignedPdfTextMatch,
} from "./textNormalization";
export { HighlightContainer } from "./HighlightContainer";
export { activeHighlightStore } from "./activeHighlightStore";
export { usePdfSearch } from "./usePdfSearch";
export { PdfToolbar } from "./PdfToolbar";
export { PdfBottomControls } from "./PdfBottomControls";
export { findTextPages, createTextHighlightOverlays, removeHighlightOverlays, computeScaledPositionFromTextLayer } from "./findTextPosition";
export {
	getAssistantHighlightBackgroundRgba,
	getUserHighlightBackgroundRgba,
	PDF_TEXT_SELECTION_FILL,
	USER_HIGHLIGHT_FILL,
	USER_HIGHLIGHT_RGBA,
} from "./highlightColors";
