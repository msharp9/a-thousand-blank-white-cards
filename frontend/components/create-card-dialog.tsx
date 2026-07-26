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
import {
  Popover,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import type { CardSnapshot, ClientMsg, PreviewResult } from "@/lib/types";

interface CreateCardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  send: (msg: ClientMsg) => void;
  previewResult: PreviewResult | null;
  caption?: string;
  card?: CardSnapshot | null;
}

export function CreateCardDialog({
  open,
  onOpenChange,
  send,
  previewResult,
  caption = "This card joins the shared deck.",
  card,
}: CreateCardDialogProps) {
  const [title, setTitle] = useState(card?.title ?? "");
  const [description, setDescription] = useState(card?.description ?? "");
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
    const art = creatorRef.current?.getArt() ?? undefined;
    send(
      card
        ? {
            type: "redraft_card",
            card_id: card.id,
            title: title.trim(),
            description: description.trim(),
            art,
          }
        : {
            type: "create_card",
            title: title.trim(),
            description: description.trim(),
            art,
          },
    );
    setTitle("");
    setDescription("");
    creatorRef.current?.reset();
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-authoring-dialog
        className="authoring-dialog grid-rows-[auto_minmax(0,1fr)_min-content] overflow-hidden sm:max-w-3xl"
      >
        <DialogHeader data-authoring-header className="pr-10">
          <DialogTitle>{card ? "Revise card" : "Create a card"}</DialogTitle>
        </DialogHeader>
        <div data-authoring-scroll className="min-h-0 overflow-hidden px-1">
          <CardCreator
            ref={creatorRef}
            title={title}
            description={description}
            onTitleChange={setTitle}
            onDescriptionChange={setDescription}
            caption={caption}
          />
        </div>
        <DialogFooter className="authoring-footer shrink-0 flex-col">
          {previewing && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Spinner /> Interpreting…
            </div>
          )}
          {previewResult && !previewing && (
            <div
              data-preview-status
              className="flex w-full min-w-0 items-center justify-center gap-2 text-xs sm:justify-end"
            >
              <span className="font-medium">Preview:</span>
              <Badge
                variant={
                  previewStatus === "applied" || previewStatus === "ok"
                    ? "default"
                    : "destructive"
                }
              >
                {previewStatus ?? "unknown"}
              </Badge>
              <PreviewDetails result={previewResult} />
            </div>
          )}
          <div className="flex w-full gap-2 sm:justify-end">
            <Button
              className="min-w-0 flex-1 sm:flex-none"
              variant="outline"
              onClick={handlePreview}
              disabled={!title.trim() || !description.trim()}
            >
              Preview
            </Button>
            <Button
              className="min-w-0 flex-1 sm:flex-none"
              onClick={handleSubmit}
              disabled={!title.trim() || !description.trim()}
            >
              Submit
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PreviewDetails({ result }: { result: PreviewResult }) {
  const hasDetails = Boolean(
    result.mechanical_reason ||
    result.correlation_id ||
    result.program ||
    result.snippet,
  );
  if (!hasDetails) return null;

  return (
    <Popover>
      <PopoverTrigger className="font-bold text-link underline underline-offset-2">
        Details
      </PopoverTrigger>
      <PopoverContent
        data-preview-details
        side="top"
        className="scrollbar-hidden max-h-[min(70dvh,28rem)] w-[min(32rem,calc(100vw-1.5rem))] overflow-y-auto text-xs"
      >
        <PopoverTitle className="mb-2">Preview details</PopoverTitle>
        <div className="flex flex-col gap-2">
          {result.mechanical_reason && <p>{result.mechanical_reason}</p>}
          {result.correlation_id && (
            <p className="font-mono text-[10px] text-muted-foreground">
              Reference: {result.correlation_id}
            </p>
          )}
          {result.program && (
            <pre className="whitespace-pre-wrap">{result.program}</pre>
          )}
          {result.snippet && (
            <pre className="whitespace-pre-wrap">{result.snippet}</pre>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
