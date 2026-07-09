"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import type { ClientMsg } from "@/lib/types";

interface BlankDrawPromptProps {
  blankCardId: string;
  send: (msg: ClientMsg) => void;
  previewResult: {
    program?: string | null;
    snippet?: string | null;
    verdict: string;
  } | null;
}

export function BlankDrawPrompt({
  blankCardId,
  send,
  previewResult,
}: BlankDrawPromptProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    if (previewResult) setPreviewing(false);
  }, [previewResult]);

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
    });
    setTitle("");
    setDescription("");
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border-2 border-primary bg-primary/5 p-4">
      <div className="flex items-center gap-2">
        <Badge>Blank draw!</Badge>
        <p className="text-sm font-medium">
          You drew a blank card — create it now to continue.
        </p>
      </div>
      <Input
        placeholder="Card title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <Textarea
        placeholder="Describe the rule…"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={3}
      />
      {previewing && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner /> Interpreting…
        </div>
      )}
      {previewResult && !previewing && (
        <div className="rounded border bg-muted/20 p-2 text-xs">
          <span className="font-medium">Preview verdict: </span>
          <Badge
            variant={previewResult.verdict === "ok" ? "default" : "destructive"}
          >
            {previewResult.verdict}
          </Badge>
        </div>
      )}
      <div className="flex gap-2">
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
          Play this card
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground">
        Card id: {blankCardId}
      </p>
    </div>
  );
}
