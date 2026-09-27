import { cn } from "@/lib/utils";

/**
 * The logo mark: an open book, two curved pages either side of a 2-unit spine.
 * Drawn in `currentColor` (brand by default) so it follows the light/dark
 * theme; `public/openpaper.svg` is the same shape with the light brand colour
 * baked in, `public/icon.svg` the favicon tile. 24-unit grid: page edges and
 * the spine gap sit on whole units, so it stays crisp at 24px.
 */
export function OpenPaperMark({ size = 24, className }: { size?: number; className?: string }) {
	return (
		<svg
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			width={size}
			height={size}
			fill="currentColor"
			aria-hidden
			className={cn("shrink-0 text-brand", className)}
		>
			<path d="M11 7C8.5 5 5.5 4 2 4v14c3.5 0 6.5.7 9 2Zm2 0c2.5-2 5.5-3 9-3v14c-3.5 0-6.5.7-9 2Z" />
		</svg>
	);
}
