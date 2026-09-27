"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { LogOut, MessageCircleQuestion, Monitor, Moon, Settings, Sun } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth, type User } from "@/lib/auth";
import { cn } from "@/lib/utils";

function initials(user: User) {
    const source = (user.name || user.email || "?").trim();
    const parts = source.split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** The avatar at the right of the header: account, settings, theme, sign out. */
export function UserMenu() {
    const router = useRouter();
    const pathname = usePathname();
    const { user, logout } = useAuth();
    const { theme, setTheme } = useTheme();
    if (!user) return null;

    const handleLogout = async () => {
        await logout();
        router.push("/login");
    };

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <button
                    type="button"
                    aria-label="Account menu"
                    className={cn(
                        "flex size-8 shrink-0 items-center justify-center rounded-full outline-none transition-[box-shadow,scale] duration-150 ease-out-soft hover:ring-2 hover:ring-border focus-visible:ring-2 focus-visible:ring-ring/50 motion-safe:active:scale-95",
                        // Settings has no nav tab; the avatar marks it instead.
                        pathname.startsWith("/settings") && "ring-2 ring-border"
                    )}
                >
                    <Avatar className="size-7">
                        {user.picture && <AvatarImage src={user.picture} alt={user.name || user.email} />}
                        <AvatarFallback className="bg-brand/15 text-[11px] font-semibold text-brand">
                            {initials(user)}
                        </AvatarFallback>
                    </Avatar>
                </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" sideOffset={8} className="w-60">
                <DropdownMenuLabel className="font-normal">
                    <div className="truncate text-sm font-medium">{user.name || user.email}</div>
                    {user.name && <div className="truncate text-xs text-muted-foreground">{user.email}</div>}
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                    <Link href="/settings">
                        <Settings />
                        Settings
                    </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                    <a href="https://github.com/khoj-ai/openpaper/issues" target="_blank" rel="noopener noreferrer">
                        <MessageCircleQuestion />
                        Feedback
                    </a>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Theme</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={setTheme}>
                    <DropdownMenuRadioItem value="light">
                        <Sun />
                        Light
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="dark">
                        <Moon />
                        Dark
                    </DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="system">
                        <Monitor />
                        System
                    </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void handleLogout()}>
                    <LogOut />
                    Sign out
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
