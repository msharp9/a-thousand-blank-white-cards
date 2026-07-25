import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import type { GameStateSnapshot } from "@/lib/types";

const ROOM = "ABC123";

function player(id: string, name: string, score: number, hand: string[] = []) {
  return {
    id,
    name,
    score,
    hand,
    hand_count: hand.length || 6,
    in_play: [],
    connected: true,
    conditions: {},
  };
}

function gameState(): GameStateSnapshot {
  const hand = Array.from({ length: 14 }, (_, index) => `hand-${index}`);
  const publicCards = Array.from(
    { length: 75 },
    (_, index) => `public-${index}`,
  );
  const cards = Object.fromEntries([
    ...hand.map((id, index) => [
      id,
      index === 0
        ? { id, title: "", description: "", blank: true }
        : {
            id,
            title: `Hand card ${index + 1}`,
            description: `Rule ${index + 1}`,
          },
    ]),
    ...publicCards.map((id, index) => [
      id,
      {
        id,
        title: `Public card ${String(index + 1).padStart(2, "0")}`,
        description: `A public rule numbered ${index + 1}.`,
        creator_id: `p${(index % 8) + 1}`,
      },
    ]),
  ]);

  return {
    room_code: ROOM,
    phase: "playing",
    players: [
      player("p1", "Alice", 999, hand),
      player("p2", "Bartholomew With A Very Long Name", -10),
      player("p3", "Chen", 21),
      player("p4", "Daria", 13),
      player("p5", "Elias", 8),
      player("p6", "Fatima", 5),
      player("p7", "Gus", 3),
      player("p8", "Hana", 1),
    ],
    spectators: [],
    turn_index: 0,
    turn_number: 12,
    turn_order: ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"],
    rules: {
      draw: 1,
      play: 1,
      cannot_play: {},
      end_condition: { type: "deck_empty" },
      win_condition: { kind: "highest_score" },
      extra: {},
    },
    draw_count: 1,
    deck: [],
    deck_count: 42,
    discard: publicCards,
    exiled: [],
    cards,
    house_rules: [],
    hooks: [],
    has_drawn: true,
    can_pass: false,
    setup_progress: {},
    cards_to_author: 5,
    winner_ids: [],
    epilogue_result: null,
    history_events: [],
    log: Array.from({ length: 30 }, (_, index) => `Earlier event ${index + 1}`),
    pending_interaction: null,
    pending_play: null,
    turn_timer: null,
  };
}

async function openMockRoom(page: Page, installClock = false) {
  if (installClock) await page.clock.install();
  const clientMessages: Record<string, unknown>[] = [];
  let socket: WebSocketRoute | undefined;

  await page.addInitScript(
    ({ room }) => {
      sessionStorage.setItem(`tbwc_player_id:${room}`, "p1");
      localStorage.setItem("tbwc_player_name", "Alice");
    },
    { room: ROOM },
  );

  await page.routeWebSocket(`ws://localhost:8000/ws/${ROOM}`, (ws) => {
    socket = ws;
    ws.onMessage((message) => {
      const parsed = JSON.parse(String(message)) as Record<string, unknown>;
      clientMessages.push(parsed);
      if (parsed.type === "join") {
        ws.send(JSON.stringify({ type: "state", state: gameState() }));
      }
    });
  });

  await page.goto(`/room/${ROOM}`);
  await expect(page.getByRole("button", { name: "Scores" })).toBeVisible();

  return {
    clientMessages,
    push(message: unknown) {
      if (!socket) throw new Error("Mock game socket is not connected");
      socket.send(JSON.stringify(message));
    },
  };
}

test("large hands and navigation stay inside the phone viewport", async ({
  page,
}) => {
  await openMockRoom(page);

  const geometry = await page.evaluate(() => {
    const game = document.querySelector<HTMLElement>("[data-game-scroll]")!;
    const hand = document.querySelector<HTMLElement>("[data-hand-rail]")!;
    const navButtons = [
      ...document.querySelectorAll<HTMLElement>(
        "nav[aria-label='Game views'] button",
      ),
    ].map((button) => button.getBoundingClientRect());
    return {
      viewportWidth: window.visualViewport?.width ?? window.innerWidth,
      documentClientWidth: document.documentElement.clientWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      gameClientWidth: game.clientWidth,
      gameScrollWidth: game.scrollWidth,
      handClientWidth: hand.clientWidth,
      handScrollWidth: hand.scrollWidth,
      nav: navButtons.map(({ left, right, height }) => ({
        left,
        right,
        height,
      })),
    };
  });

  expect(geometry.documentScrollWidth).toBeLessThanOrEqual(
    geometry.documentClientWidth + 1,
  );
  expect(geometry.gameScrollWidth).toBeLessThanOrEqual(
    geometry.gameClientWidth + 1,
  );
  const compact = geometry.viewportWidth < 640;
  if (compact) {
    expect(geometry.handScrollWidth).toBeGreaterThan(geometry.handClientWidth);
  }
  for (const button of geometry.nav) {
    expect(button.left).toBeGreaterThanOrEqual(0);
    expect(button.right).toBeLessThanOrEqual(geometry.viewportWidth + 1);
    if (compact) expect(button.height).toBeGreaterThanOrEqual(44);
  }

  await page.locator("[data-hand-rail]").evaluate((rail) => {
    rail.scrollLeft = rail.scrollWidth;
  });
  await expect(page.getByText("Hand card 14")).toBeVisible();

  await page.setViewportSize({ width: 844, height: 390 });
  await expect
    .poll(() =>
      page.evaluate(() => ({
        scale: window.visualViewport?.scale ?? 1,
        width: window.visualViewport?.width ?? window.innerWidth,
      })),
    )
    .toMatchObject({ scale: 1, width: 844 });
  const landscapeWidths = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(landscapeWidths.scrollWidth).toBeLessThanOrEqual(
    landscapeWidths.clientWidth + 1,
  );
});

test("live notices remain visible while the game is scrolled to the log", async ({
  page,
}) => {
  const room = await openMockRoom(page, true);
  const game = page.locator("[data-game-scroll]");
  await game.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  const scrollTop = await game.evaluate((node) => node.scrollTop);

  room.push({
    type: "effect_applied",
    log_entry: "🤖 That card has the strategic grace of a falling sandwich.",
  });
  room.push({
    type: "effect_applied",
    log_entry: "🤖 Against all odds, the sandwich has a sequel.",
  });
  room.push({
    type: "dice_roll",
    actor_id: "p1",
    sides: 6,
    values: [2, 6],
    total: 8,
    card_id: "public-1",
  });
  room.push({
    type: "dice_roll",
    actor_id: "p2",
    sides: 6,
    values: [4],
    total: 4,
    card_id: "public-2",
  });

  const arbiterBubble = page
    .locator('[data-notice-lane="arbiter"]')
    .getByText(/falling sandwich/);
  await expect(arbiterBubble).toBeVisible();
  await expect(page.getByText(/Alice rolls 2d6/)).toBeVisible();
  expect(await game.evaluate((node) => node.scrollTop)).toBe(scrollTop);

  for (const lane of ["top", "arbiter"]) {
    const box = await page
      .locator(`[data-notice-lane="${lane}"]`)
      .boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(
      await page.evaluate(() => window.visualViewport?.height ?? innerHeight),
    );
  }

  await page
    .locator('[data-notice-lane="arbiter"]')
    .getByRole("button", { name: "Dismiss notification" })
    .click();
  await expect(arbiterBubble).toBeHidden();
  const sequelBubble = page
    .locator('[data-notice-lane="arbiter"]')
    .getByText(/sandwich has a sequel/);
  await expect(sequelBubble).toBeVisible();

  await page.clock.fastForward(5100);
  await expect(page.getByText(/Alice rolls 2d6/)).toBeHidden();
  await expect(page.getByText(/Bartholomew.*rolls 1d6/)).toBeVisible();
  await expect(sequelBubble).toBeVisible();
  await page.clock.fastForward(2000);
  await expect(sequelBubble).toBeHidden();
  await page.clock.fastForward(3100);
  await expect(page.getByText(/Bartholomew.*rolls 1d6/)).toBeHidden();
  await expect(
    page.locator("[data-game-scroll]").getByText(/falling sandwich/),
  ).toBeAttached();
});

test("mobile overlays and blank-card authoring remain compact and reachable", async ({
  page,
}) => {
  const room = await openMockRoom(page);

  await page.getByRole("button", { name: "Gallery" }).click();
  const gallery = page.locator("[data-gallery-grid]");
  await expect(gallery).toBeVisible();
  const compact = (await page.viewportSize())!.width < 640;
  expect(await gallery.locator(":scope > *").count()).toBe(compact ? 24 : 60);
  const firstCards = await gallery.locator(":scope > *").evaluateAll((items) =>
    items.slice(0, 4).map((item) => {
      const box = item.getBoundingClientRect();
      return { x: Math.round(box.x), width: Math.round(box.width) };
    }),
  );
  if (compact) expect(new Set(firstCards.map((card) => card.x)).size).toBe(2);
  expect(firstCards.every((card) => card.width <= 164)).toBe(true);
  await page.getByRole("button", { name: "Close gallery" }).click();

  await page.getByRole("button", { name: "Scores" }).click();
  await expect(
    page
      .locator("[data-scoreboard-list]")
      .getByText("Bartholomew With A Very Long Name"),
  ).toBeVisible();
  const scoreBounds = await page
    .locator("[data-scoreboard-list]")
    .evaluate((list) => {
      const viewportWidth = window.visualViewport?.width ?? window.innerWidth;
      return [...list.children].every((child) => {
        const box = child.getBoundingClientRect();
        return box.left >= 0 && box.right <= viewportWidth + 1;
      });
    });
  expect(scoreBounds).toBe(true);
  await page.getByRole("button", { name: "Close scoreboard" }).click();

  await page.getByRole("button", { name: /Blank/ }).click();
  await page
    .getByRole("button", { name: "Fill in & play", exact: true })
    .click();
  await page.getByLabel("Card title").fill("Pocket Volcano");
  await page.getByLabel("Card rules").fill("Everyone loses one point.");
  const canvas = page.getByLabel("Card drawing canvas");
  await canvas.scrollIntoViewIfNeeded();
  const ratio = await canvas.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return box.width / box.height;
  });
  expect(ratio).toBeCloseTo(6 / 5, 1);
  // A tap is a legitimate one-point stroke and exercises the keyboard-blur
  // path without relying on coordinates outside the nested dialog scroller.
  await canvas.click();
  expect(await canvas.evaluate((node) => document.activeElement === node)).toBe(
    true,
  );
  if (compact) {
    const portrait = page.viewportSize()!;
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(page.getByLabel("Card title")).toHaveValue("Pocket Volcano");
    await expect(page.getByLabel("Card rules")).toHaveValue(
      "Everyone loses one point.",
    );
    await page.setViewportSize(portrait);
  }
  await page.getByRole("button", { name: "Play this card" }).click();
  await expect
    .poll(() =>
      room.clientMessages.find(
        (message) =>
          message.type === "play" && message.title === "Pocket Volcano",
      ),
    )
    .toMatchObject({
      card_id: "hand-0",
      description: "Everyone loses one point.",
    });
  const play = room.clientMessages.find(
    (message) => message.type === "play" && message.title === "Pocket Volcano",
  );
  expect(String(play?.art)).toMatch(/^data:image\/png;base64,/);
});
