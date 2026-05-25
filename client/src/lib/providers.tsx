'use client'

import { useIsDarkMode } from "@/hooks/useDarkMode"

// Placeholder provider that previously wired up posthog-js. Kept as a thin
// wrapper so the layouts can stay untouched if/when a browser-side
// observability SDK (e.g. @pydantic/logfire-browser) is reintroduced.
export function AnalyticsProvider({ children }: { children: React.ReactNode }) {
    return <>{children}</>
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
    // Actually use the hook - this ensures React is aware of the dark mode state
    const {  } = useIsDarkMode();

    return <>{children}</>;
}
