"use client"

import {
    ChevronsUpDown,
    FileText,
    FolderKanban,
    Compass,
    Home,
    LogOut,
    MessageCircleQuestion,
    Moon,
    Settings,
    Sun,
    TelescopeIcon,
    User as UserIcon,
} from "lucide-react";

import {
    Sidebar,
    SidebarContent,
    SidebarFooter,
    SidebarGroup,
    SidebarGroupContent,
    SidebarMenu,
    SidebarMenuButton,
    SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useMemo } from "react";
import useSWR from "swr";
import { api, unwrap } from "@/lib/api/client";
import { useRouter } from "next/navigation";
import { useAuth, User } from "@/lib/auth";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import {
    Sheet,
    SheetContent,
    SheetTrigger,
} from "@/components/ui/sheet";
import { useTheme } from "next-themes";
import Link from "next/link";
import { useIsMobile } from "@/hooks/use-mobile";
import { CollapsibleSidebarMenu } from "./CollapsibleSidebarMenu";

// Menu items.
const items = [
    {
        title: "Home",
        url: "/",
        icon: Home,
        requiresAuth: false,
    },
    {
        title: "Library",
        url: "/papers",
        icon: FileText,
        requiresAuth: true,
    },
    {
        title: "Projects",
        url: "/projects",
        icon: FolderKanban,
        requiresAuth: true,
    },
    {
        title: "Discover",
        url: "/discover",
        icon: Compass,
        requiresAuth: true,
        isNew: true,
    },
]

const UserMenuContent = ({
    user,
    handleLogout,
    toggleDarkMode,
    darkMode,
}: {
    user: User,
    handleLogout: () => void,
    toggleDarkMode: () => void,
    darkMode: boolean
}) => (
    <div className="flex flex-col gap-1">
        <div className="flex items-center gap-3 p-3">
            <Avatar className="h-10 w-10">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {user.picture ? (<img src={user.picture} alt={user.name || user.email} />) : (<UserIcon size={24} />)}
            </Avatar>
            <div>
                <h3 className="font-medium">{user.name || user.email}</h3>
                <p className="text-sm text-muted-foreground">{user.email}</p>
            </div>
        </div>
        <Link href="/settings" className="w-full">
            <Button variant="ghost" className="w-full justify-start">
                <Settings size={16} className="mr-2" />
                Settings
            </Button>
        </Link>
        {/* Feedback section */}
        <Link href="https://github.com/khoj-ai/openpaper/issues" target="_blank" className="w-full">
            <Button variant="ghost" className="w-full justify-start">
                <MessageCircleQuestion size={16} className="mr-2" />
                Feedback
            </Button>
        </Link>
        {/* Dark Mode Toggle */}
        <Button onClick={toggleDarkMode} variant="ghost" className="w-full justify-start">
            {darkMode ? <Sun size={16} className="mr-2" /> : <Moon size={16} className="mr-2" />}
            {darkMode ? 'Light Mode' : 'Dark Mode'}
        </Button>
        <Button
            variant="ghost"
            className="w-full justify-start"
            onClick={handleLogout}
        >
            <LogOut size={16} className="mr-2" />
            Sign out
        </Button>
    </div>
)

export function AppSidebar() {
    const router = useRouter();
    const { user, logout } = useAuth();
    const { resolvedTheme, setTheme } = useTheme();
    const darkMode = resolvedTheme === "dark";
    const toggleDarkMode = () => setTheme(darkMode ? "light" : "dark");
    const isMobile = useIsMobile();

    const onFetchError = (error: unknown) => console.error("Error fetching sidebar data:", error);
    const { data: activePapers } = useSWR(
        user ? ["/api/paper/active"] : null,
        () => unwrap(api.GET("/api/paper/active")),
        { onError: onFetchError },
    );
    const { data: projectsData } = useSWR(
        user ? ["/api/projects"] : null,
        () => unwrap(api.GET("/api/projects")),
        { onError: onFetchError },
    );
    const allPapers = useMemo(
        () =>
            user && activePapers
                ? [...activePapers.papers].sort(
                    (a, b) => new Date(b.created_at || "").getTime() - new Date(a.created_at || "").getTime(),
                )
                : [],
        [user, activePapers],
    );
    const projects = user && projectsData ? projectsData : [];


    const handleLogout = async () => {
        await logout();
        router.push('/login');
    }

    return (
        <Sidebar variant="floating">
            <SidebarContent>
                <SidebarGroup>
                    <SidebarGroupContent>
                        <SidebarMenu>
                            {items.map((item) => {
                                if (item.title === "Library") {
                                    return (
                                        <CollapsibleSidebarMenu
                                            key={item.title}
                                            title={item.title}
                                            icon={item.icon}
                                            url={item.url}
                                            items={allPapers}
                                            getItemUrl={(paper) => `/paper/${paper.id}`}
                                            viewAllUrl="/papers"
                                            viewAllText="View all papers"
                                            defaultOpen={true}
                                        />
                                    )
                                }
                                if (item.title === "Projects") {
                                    return (
                                        <CollapsibleSidebarMenu
                                            key={item.title}
                                            title={item.title}
                                            icon={item.icon}
                                            url={item.url}
                                            items={projects}
                                            getItemUrl={(project) => `/projects/${project.id}`}
                                            getItemName={(project) => project.title}
                                            viewAllUrl="/projects"
                                            viewAllText="View all projects"
                                            defaultOpen={false}
                                            maxItems={3}
                                        />
                                    )
                                }
                                return (
                                    <SidebarMenuItem key={item.title}>
                                        <SidebarMenuButton asChild>
                                            <Link href={item.requiresAuth && !user ? "/login" : item.url}>
                                                <item.icon />
                                                <span>{item.title}</span>
                                                {item.isNew && (
                                                    <Badge className="ml-auto text-[10px] px-1.5 py-0 bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900">
                                                        New
                                                    </Badge>
                                                )}
                                            </Link>
                                        </SidebarMenuButton>
                                    </SidebarMenuItem>
                                )
                            })}
                        </SidebarMenu>
                    </SidebarGroupContent>
                </SidebarGroup>
            </SidebarContent>
            <SidebarFooter>
                {/* User Profile (if logged in) */}
                {user && (
                    <SidebarMenuItem className="mb-2">
                        {isMobile ? (
                            <Sheet>
                                <SheetTrigger asChild>
                                    <SidebarMenuButton className="flex items-center gap-2">
                                        <span className="flex items-center gap-2 truncate">
                                            <Avatar className="h-6 w-6">
                                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                                {user.picture ? <img src={user.picture} alt={user.name || user.email} /> : <UserIcon size={16} />}
                                            </Avatar>
                                            <span className="truncate">{user.name || user.email}</span>
                                        </span>
                                        <ChevronsUpDown className="h-4 w-4 ml-auto" />
                                    </SidebarMenuButton>
                                </SheetTrigger>
                                <SheetContent side="bottom">
                                    <UserMenuContent user={user} handleLogout={handleLogout} toggleDarkMode={toggleDarkMode} darkMode={darkMode} />
                                </SheetContent>
                            </Sheet>
                        ) : (
                            <Popover>
                                <PopoverTrigger asChild>
                                    <SidebarMenuButton className="flex items-center gap-2">
                                        <span className="flex items-center gap-2 truncate">
                                            <Avatar className="h-6 w-6">
                                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                                {user.picture ? (<img src={user.picture} alt={user.name || user.email} />) : (<UserIcon size={16} />)}
                                            </Avatar>
                                            <span className="truncate">{user.name || user.email}</span>
                                        </span>
                                        <ChevronsUpDown className="h-4 w-4 ml-auto" />
                                    </SidebarMenuButton>
                                </PopoverTrigger>
                                <PopoverContent className="w-60 p-1" align="start">
                                    <UserMenuContent user={user} handleLogout={handleLogout} toggleDarkMode={toggleDarkMode} darkMode={darkMode} />
                                </PopoverContent>
                            </Popover>
                        )}
                    </SidebarMenuItem>
                )}

                {/* Login button (if not logged in) */}
                {!user && (
                    <SidebarMenuItem>
                        <SidebarMenuButton asChild>
                            <a
                                href="/login"
                                className="w-full flex items-center gap-2 bg-primary text-primary-foreground hover:bg-primary/90 px-3 py-2 rounded-md transition-colors"
                            >
                                <UserIcon size={16} />
                                <span className="font-medium">Sign In</span>
                            </a>
                        </SidebarMenuButton>
                    </SidebarMenuItem>
                )}
            </SidebarFooter>
        </Sidebar >
    )
}
