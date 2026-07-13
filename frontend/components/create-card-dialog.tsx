"use client";

import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CardCreator, type CardCreatorHandle } from "@/components/card-creator";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import type { ClientMsg, PreviewResult } from "@/lib/types";

interface CreateCardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  send: (msg: ClientMsg) => void;
  previewResult: PreviewResult | null;
  caption?: string;
}

export function CreateCardDialog({
  open,
  onOpenChange,
  send,
  previewResult,
  caption = "This card joins the shared deck.",
}: CreateCardDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [lastResult, setLastResult] = useState(previewResult);
  const creatorRef = useRef<CardCreatorHandle>(null);
  const previewStatus =
    previewResult?.mechanical_status ?? previewResult?.verdict;

  // Stop the spinner when a new preview result arrives. Adjusting state during
  // render (rather than in an effect) is React's recommended pattern for
  // reacting to a changed prop and avoids a cascading re-render.
  if (previewResult !== lastResult) {
    setLastResult(previewResult);
    if (previewResult) setPreviewing(false);
  }

  function handlePreview() {
    if (!title.trim() || !description.trim()) return;
    setPreviewing(true);
    send({
      type: "preview_card",
      title: title.trim(),
      description: description.trim(),
    });
  }

  function handleSubmit() {
    if (!title.trim() || !description.trim()) return;
    send({
      type: "create_card",
      title: title.trim(),
      description: description.trim(),
      art: creatorRef.current?.getArt() ?? undefined,
    });
    setTitle("");
    setDescription("");
    creatorRef.current?.reset();
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Create a card</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <CardCreator
            ref={creatorRef}
            title={title}
            description={description}
            onTitleChange={setTitle}
            onDescriptionChange={setDescription}
            caption={caption}
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
                <Badge
                  variant={
                    previewStatus === "applied" || previewStatus === "ok"
                      ? "default"
                      : "destructive"
                  }
                >
                  {previewStatus}
                </Badge>
              </div>
              {previewResult.mechanical_reason && (
                <p className="text-muted-foreground">
                  {previewResult.mechanical_reason}
                </p>
              )}
              {previewResult.correlation_id && (
                <p className="font-mono text-[10px] text-muted-foreground">
                  Reference: {previewResult.correlation_id}
                </p>
              )}
              {previewResult.program && (
                <pre className="whitespace-pre-wrap">
                  {previewResult.program}
                </pre>
              )}
              {previewResult.snippet && (
                <pre className="whitespace-pre-wrap">
                  {previewResult.snippet}
                </pre>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={handlePreview}
            disabled={!title.trim() || !description.trim()}
          >
            Preview
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!title.trim() || !description.trim()}
          >
            Submit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
