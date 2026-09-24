"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api, unwrap } from "@/lib/api/client";
import { toast } from "sonner";
import { PlusCircle } from "lucide-react";

interface TagSelectorProps {
    paperIds: string[];
    onTagsApplied: () => void;
}

export function TagSelector({ paperIds, onTagsApplied }: TagSelectorProps) {
    const { data: tags = [], mutate: mutateTags } = useSWR(
        ["/api/paper/tag/"],
        () => unwrap(api.GET("/api/paper/tag/")),
        {
            onError: (error) => {
                console.error("Failed to fetch tags", error);
                toast.error("Failed to load tags.");
            },
        },
    );
    const [searchTerm, setSearchTerm] = useState("");
    const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
    const [newTagName, setNewTagName] = useState("");

    const filteredTags = useMemo(() => {
        return tags.filter((tag) =>
            tag.name.toLowerCase().includes(searchTerm.toLowerCase())
        );
    }, [tags, searchTerm]);

    const handleTagSelection = (tagId: string, checked: boolean) => {
        const newSelectedTags = new Set(selectedTags);
        if (checked) {
            newSelectedTags.add(tagId);
        } else {
            newSelectedTags.delete(tagId);
        }
        setSelectedTags(newSelectedTags);
    };

    const handleCreateTag = async () => {
        if (!newTagName.trim()) return;
        try {
            const newTag = await unwrap(api.POST("/api/paper/tag/", {
                body: { name: newTagName },
            }));
            mutateTags((current) => [...(current ?? []), newTag], { revalidate: false });
            setNewTagName("");
            toast.success(`Tag "${newTag.name}" created.`);
        } catch (error) {
            console.error("Failed to create tag", error);
            toast.error("Failed to create tag.");
        }
    };

    const handleApplyTags = async () => {
        try {
            await unwrap(api.POST("/api/paper/tag/bulk", {
                body: {
                    paper_ids: paperIds,
                    tag_ids: Array.from(selectedTags),
                },
            }));
            onTagsApplied();
        } catch (error) {
            console.error("Failed to apply tags", error);
            toast.error("Failed to apply tags.");
        }
    };

    return (
        <div className="p-4 space-y-4">
            <Input
                placeholder="Filter tags..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
            />
            <ScrollArea className="h-48">
                <div className="space-y-2">
                    {filteredTags.map((tag) => (
                        <div key={tag.id} className="flex items-center space-x-2">
                            <Checkbox
                                id={`tag-${tag.id}`}
                                checked={selectedTags.has(tag.id)}
                                onCheckedChange={(checked) =>
                                    handleTagSelection(tag.id, !!checked)
                                }
                            />
                            <label
                                htmlFor={`tag-${tag.id}`}
                                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                            >
                                {tag.name}
                            </label>
                        </div>
                    ))}
                </div>
            </ScrollArea>
            <div className="flex items-center space-x-2">
                <Input
                    placeholder="Create new tag..."
                    value={newTagName}
                    onChange={(e) => setNewTagName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleCreateTag()}
                />
                <Button variant="outline" size="icon" onClick={handleCreateTag}>
                    <PlusCircle className="h-4 w-4" />
                </Button>
            </div>
            <Button
                onClick={handleApplyTags}
                className="w-full"
                disabled={selectedTags.size === 0}
            >
                Apply Tags
            </Button>
        </div>
    );
}
