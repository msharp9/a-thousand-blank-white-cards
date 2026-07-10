"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface PlayBlankDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Called with the authored title+description when the player commits. The
  // caller sends the play (carrying these), which fills in the blank and plays
  // it this turn.
  onPlay: (title: string, description: string) => void;
}

// Author-on-play dialog for a BLANK card. The game is *A Thousand Blank White
// Cards*: a blank is played by authoring it. Submitting sends a `play` carrying
// the authored title+description; the backend fills in the card and interprets
// it as the played card.
export function PlayBlankDialog({
  open,
  onOpenChange,
  onPlay,
}: PlayBlankDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");

  function reset() {
    setTitle("");
    setDescription("");
  }

  function handlePlay() {
    if (!title.trim() || !description.trim()) return;
    onPlay(title.trim(), description.trim());
    reset();
    onOpenChange(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Fill in your blank card</DialogTitle>
          <DialogDescription>
            Write the card you want to play. The referee interprets it and plays
            it this turn.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Input
            placeholder="Card title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Textarea
            placeholder="Describe the rule… e.g. 'Everyone loses 5 points'"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
          />
        </div>
        <DialogFooter>
          <Button
            onClick={handlePlay}
            disabled={!title.trim() || !description.trim()}
          >
            Play this card
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
