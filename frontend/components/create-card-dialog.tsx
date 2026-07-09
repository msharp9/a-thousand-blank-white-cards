"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import type { ClientMsg } from "@/lib/types";

interface CreateCardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  send: (msg: ClientMsg) => void;
  previewResult: { program?: string | null; snippet?: string | null; verdict: string } | null;
}

export function CreateCardDialog({ open, onOpenChange, send, previewResult }: CreateCardDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [previewing, setPreviewing] = useState(false);

  // Stop the spinner when a preview result arrives.
  useEffect(() => {
    if (previewResult) setPreviewing(false);
  }, [previewResult]);

  function handlePreview() {
    if (!title.trim() || !description.trim()) return;
    setPreviewing(true);
    send({ type: "preview_card", title: title.trim(), description: description.trim() });
  }

  function handleSubmit() {
    if (!title.trim() || !description.trim()) return;
    send({ type: "create_card", title: title.trim(), description: description.trim() });
    setTitle("");
    setDescription("");
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create a card</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Input placeholder="Card title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <Textarea
            placeholder="Describe the rule… e.g. 'Everyone loses 5 points'"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
          />
          {previewing && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Spinner /> Interpreting…
            </div>
          )}
          {previewResult && !previewing && (
            <div className="flex flex-col gap-1 rounded-lg border bg-muted/20 p-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-medium">Preview:</span>
                <Badge variant={previewResult.verdict === "ok" ? "default" : "destructive"}>
                  {previewResult.verdict}
                </Badge>
              </div>
              {previewResult.program && <pre className="whitespace-pre-wrap">{previewResult.program}</pre>}
              {previewResult.snippet && <pre className="whitespace-pre-wrap">{previewResult.snippet}</pre>}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={handlePreview} disabled={!title.trim() || !description.trim()}>
            Preview
          </Button>
          <Button onClick={handleSubmit} disabled={!title.trim() || !description.trim()}>
            Submit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
