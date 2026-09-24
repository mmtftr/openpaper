"use client";

import { useStickToBottomContext } from "use-stick-to-bottom";

interface LoadEarlierProps {
    isLoading: boolean;
    onLoad: () => Promise<void>;
}

/**
 * "Load earlier messages". Lives inside <Conversation> so it can read the
 * StickToBottom scroll container and keep the viewport where it was when
 * older messages are prepended.
 */
export function LoadEarlier({ isLoading, onLoad }: LoadEarlierProps) {
    const { scrollRef } = useStickToBottomContext();

    const handleClick = async () => {
        const el = scrollRef.current;
        const beforeHeight = el?.scrollHeight ?? 0;
        const beforeTop = el?.scrollTop ?? 0;
        await onLoad();
        requestAnimationFrame(() => {
            const node = scrollRef.current;
            if (!node) return;
            const delta = node.scrollHeight - beforeHeight;
            node.scrollTop = beforeTop + delta;
        });
    };

    return (
        <div className="text-center">
            {isLoading ? (
                <span className="text-xs text-muted-foreground">
                    Loading earlier messages…
                </span>
            ) : (
                <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground"
                    onClick={handleClick}
                >
                    Load earlier messages
                </button>
            )}
        </div>
    );
}
