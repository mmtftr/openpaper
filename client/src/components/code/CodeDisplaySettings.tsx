"use client";

import { SettingsIcon } from "lucide-react";

import { setCodeWrap, useCodeWrap } from "@/hooks/useCodeWrap";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";

/**
 * Display preferences for code — currently just line wrapping, which applies
 * to the viewer and to fenced code blocks in chat alike.
 */
export function CodeDisplaySettings() {
    const wrap = useCodeWrap();

    return (
        <Popover>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7 text-muted-foreground hover:text-foreground"
                    title="Code display settings"
                    aria-label="Code display settings"
                >
                    <SettingsIcon className="size-3.5" />
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" side="bottom" className="w-56">
                <div className="flex items-center justify-between gap-3">
                    <Label
                        htmlFor="code-wrap-toggle"
                        className="text-xs font-normal"
                    >
                        Line wrap
                    </Label>
                    <Switch
                        id="code-wrap-toggle"
                        checked={wrap}
                        onCheckedChange={setCodeWrap}
                        aria-label="Wrap long code lines"
                    />
                </div>
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                    Wrap long lines instead of scrolling sideways. Applies to
                    code in chat too.
                </p>
            </PopoverContent>
        </Popover>
    );
}
