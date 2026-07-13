"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { CardCreator, type CardCreatorHandle } from "@/components/card-creator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface PlayBlankDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Called with the authored title+description (and drawn art, if any) when
  // the player commits. The caller sends the play (carrying these), which
  // fills in the blank and plays it this turn.
  onPlay: (title: string, description: string, art?: string) => void;
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
  const creatorRef = useRef<CardCreatorHandle>(null);

  function reset() {
    setTitle("");
    setDescription("");
    creatorRef.current?.reset();
  }

  function handlePlay() {
    if (!title.trim() || !description.trim()) return;
    onPlay(
      title.trim(),
      description.trim(),
      creatorRef.current?.getArt() ?? undefined,
    );
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
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Fill in your blank card</DialogTitle>
          <DialogDescription>
            Write the card you want to play. The arbiter interprets it and plays
            it this turn.
          </DialogDescription>
        </DialogHeader>
        <CardCreator
          ref={creatorRef}
          title={title}
          description={description}
          onTitleChange={setTitle}
          onDescriptionChange={setDescription}
          caption="This card is played this turn."
        />
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
