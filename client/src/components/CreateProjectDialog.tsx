"use client";

import { useState } from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

interface CreateProjectDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSubmit: (title: string, description: string) => void;
}

export function CreateProjectDialog({ open, onOpenChange, onSubmit }: CreateProjectDialogProps) {
    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!title.trim()) return;
        onSubmit(title, description);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Create a new project</DialogTitle>
                    <DialogDescription>
                        Enter a title and description for your new project.
                    </DialogDescription>
                </DialogHeader>
                <form id="create-project" onSubmit={handleSubmit} className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="title">Title</Label>
                        <Input
                            id="title"
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder="Project title"
                            autoFocus
                            autoComplete="off"
                        />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="description">Description</Label>
                        <Textarea
                            id="description"
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder="Project description"
                        />
                    </div>
                </form>
                <DialogFooter>
                    <Button variant="ghost" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button type="submit" form="create-project" disabled={!title.trim()}>Create</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
