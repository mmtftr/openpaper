'use client'

import { useIsDarkMode } from "@/hooks/useDarkMode"
import { registerOfflineServiceWorker, replayOutbox } from "@/lib/offline";
import { useEffect } from "react";

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

export function OfflineProvider({ children }: { children: React.ReactNode }) {
    useEffect(() => {
        registerOfflineServiceWorker();
        const handleOnline = () => {
            replayOutbox().catch((error) => {
                console.error("Offline replay failed", error);
            });
        };
        window.addEventListener("online", handleOnline);
        if (navigator.onLine) handleOnline();
        return () => window.removeEventListener("online", handleOnline);
    }, []);

    return <>{children}</>;
}
