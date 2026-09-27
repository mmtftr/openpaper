import { cn } from "@/lib/utils";

/**
 * The logo mark: a dog-eared page with one highlighted line knocked out. Drawn
 * in `currentColor` (brand by default) so it follows the light/dark theme;
 * `public/openpaper.svg` is the same shape with the light brand colour baked in.
 * 24-unit grid, pixel-aligned at 24px.
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
			<path
				fillRule="evenodd"
				d="M7 2h7l5 5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm0 11v3h10v-3Z"
			/>
		</svg>
	);
}
