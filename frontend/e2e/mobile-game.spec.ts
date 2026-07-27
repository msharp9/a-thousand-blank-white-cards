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
    mode: "in_person",
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

function setupState(): GameStateSnapshot {
  return {
    ...gameState(),
    phase: "setup",
    has_drawn: false,
    setup_progress: { p1: 0 },
    setup_draft_progress: {},
    cards_to_author: 20,
  };
}

async function openMockRoom(
  page: Page,
  installClock = false,
  snapshot = gameState(),
) {
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
        ws.send(JSON.stringify({ type: "state", state: snapshot }));
      }
    });
  });

  await page.goto(`/room/${ROOM}`);
  await expect(
    snapshot.phase === "setup"
      ? page.getByRole("button", { name: "Author a card" })
      : page.getByRole("button", { name: "Scores" }),
  ).toBeVisible();

  return {
    clientMessages,
    push(message: unknown) {
      if (!socket) throw new Error("Mock game socket is not connected");
      socket.send(JSON.stringify(message));
    },
  };
}

async function expectAuthoringFits(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const viewportHeight =
          window.visualViewport?.height ?? window.innerHeight;
        const authoring = document.querySelector<HTMLElement>(
          "[data-authoring-dialog]",
        )!;
        const content = authoring.getBoundingClientRect();
        const card = document
          .querySelector<HTMLElement>(".card-creator-card")!
          .getBoundingClientRect();
        const surface = document
          .querySelector<HTMLElement>("[data-authoring-scroll]")!
          .getBoundingClientRect();
        const visibleButtons = [
          ...authoring.querySelectorAll<HTMLElement>("button"),
        ].filter((node) => node.offsetParent !== null);
        return (
          content.bottom <= viewportHeight + 1 &&
          card.top >= surface.top - 1 &&
          card.bottom <= surface.bottom + 1 &&
          visibleButtons.every(
            (button) =>
              button.getBoundingClientRect().bottom <= viewportHeight + 1,
          )
        );
      }),
    )
    .toBe(true);

  const geometry = await page.evaluate(() => {
    const viewportWidth = window.visualViewport?.width ?? window.innerWidth;
    const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
    const selectors = [
      "[data-authoring-dialog]",
      "[data-authoring-scroll]",
      "[data-card-creator]",
    ];
    const regions = selectors.map((selector) => {
      const node = document.querySelector<HTMLElement>(selector)!;
      return {
        selector,
        clientWidth: node.clientWidth,
        clientHeight: node.clientHeight,
        scrollWidth: node.scrollWidth,
        scrollHeight: node.scrollHeight,
        scrollLeft: node.scrollLeft,
        scrollTop: node.scrollTop,
      };
    });
    const card = document
      .querySelector<HTMLElement>(".card-creator-card")!
      .getBoundingClientRect();
    const surface = document
      .querySelector<HTMLElement>("[data-authoring-scroll]")!
      .getBoundingClientRect();
    const authoring = document.querySelector<HTMLElement>(
      "[data-authoring-dialog]",
    )!;
    const controls = [
      ...authoring.querySelectorAll<HTMLElement>(
        "input, textarea, canvas, button",
      ),
    ]
      .filter((node) => node.offsetParent !== null)
      .map((node) => {
        const box = node.getBoundingClientRect();
        return {
          name:
            node.getAttribute("aria-label") ||
            node.getAttribute("title") ||
            node.textContent?.trim() ||
            node.tagName,
          left: box.left,
          top: box.top,
          right: box.right,
          bottom: box.bottom,
        };
      });
    const game = document.querySelector<HTMLElement>("[data-game-scroll]")!;
    const dialogViewport = document.querySelector<HTMLElement>(
      '[data-slot="dialog-viewport"]',
    )!;
    return {
      viewportWidth,
      viewportHeight,
      regions,
      card: {
        left: card.left,
        top: card.top,
        right: card.right,
        bottom: card.bottom,
      },
      surface: {
        left: surface.left,
        top: surface.top,
        right: surface.right,
        bottom: surface.bottom,
      },
      controls,
      gameOverflowY: getComputedStyle(game).overflowY,
      dialogViewportOverflow: [
        getComputedStyle(dialogViewport).overflowX,
        getComputedStyle(dialogViewport).overflowY,
      ],
    };
  });

  for (const region of geometry.regions) {
    // The three-pixel transformed dialog border can round outward by one
    // additional pixel during viewport rotation; larger deltas are real
    // overflow rather than paint rounding.
    const roundingTolerance = 4;
    expect(
      region.scrollWidth,
      `${region.selector} has horizontal overflow`,
    ).toBeLessThanOrEqual(region.clientWidth + roundingTolerance);
    expect(
      region.scrollHeight,
      `${region.selector} has vertical overflow`,
    ).toBeLessThanOrEqual(region.clientHeight + roundingTolerance);
    expect(region.scrollLeft).toBe(0);
    expect(region.scrollTop).toBe(0);
  }
  expect(geometry.card.left).toBeGreaterThanOrEqual(geometry.surface.left - 1);
  expect(geometry.card.top).toBeGreaterThanOrEqual(geometry.surface.top - 1);
  expect(geometry.card.right).toBeLessThanOrEqual(geometry.surface.right + 1);
  expect(geometry.card.bottom).toBeLessThanOrEqual(geometry.surface.bottom + 1);
  for (const control of geometry.controls) {
    expect(control.left, `${control.name} escapes left`).toBeGreaterThanOrEqual(
      -1,
    );
    expect(control.top, `${control.name} escapes top`).toBeGreaterThanOrEqual(
      -1,
    );
    expect(control.right, `${control.name} escapes right`).toBeLessThanOrEqual(
      geometry.viewportWidth + 1,
    );
    expect(
      control.bottom,
      `${control.name} escapes bottom`,
    ).toBeLessThanOrEqual(geometry.viewportHeight + 1);
  }
  expect(geometry.gameOverflowY).toBe("hidden");
  expect(geometry.dialogViewportOverflow).toEqual(["hidden", "hidden"]);
}

async function expectPaletteFits(page: Page, selector: string) {
  const boxes = await page.locator(selector).evaluate((palette) => {
    const viewportWidth = window.visualViewport?.width ?? window.innerWidth;
    const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
    const nodes = [palette, ...palette.querySelectorAll<HTMLElement>("button")];
    return {
      viewportWidth,
      viewportHeight,
      boxes: nodes.map((node) => {
        const box = node.getBoundingClientRect();
        return {
          left: box.left,
          top: box.top,
          right: box.right,
          bottom: box.bottom,
        };
      }),
    };
  });
  for (const box of boxes.boxes) {
    expect(box.left).toBeGreaterThanOrEqual(-1);
    expect(box.top).toBeGreaterThanOrEqual(-1);
    expect(box.right).toBeLessThanOrEqual(boxes.viewportWidth + 1);
    expect(box.bottom).toBeLessThanOrEqual(boxes.viewportHeight + 1);
  }
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

test("live notices remain visible while the game is scrolled", async ({
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
  await page.getByRole("button", { name: "History" }).click();
  await expect(
    page.locator("[data-game-panel-body]").getByText(/falling sandwich/),
  ).toBeAttached();
});

test("adaptive panels and blank-card authoring remain compact and reachable", async ({
  page,
}) => {
  const room = await openMockRoom(page);
  const wide = (await page.viewportSize())!.width >= 1280;

  await page.getByRole("button", { name: "Gallery" }).click();
  await expect(page.locator("[data-game-panel-shell]")).toHaveAttribute(
    "data-presentation",
    wide ? "sidebar" : "modal",
  );
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

  await page.getByRole("button", { name: "Host" }).click();
  const hostPanel = page.getByRole(wide ? "complementary" : "dialog", {
    name: "Host controls",
  });
  await expect(hostPanel).toBeVisible();
  const hostBounds = await hostPanel.evaluate((panel) => {
    const box = panel.getBoundingClientRect();
    const viewportWidth = window.visualViewport?.width ?? window.innerWidth;
    const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
    return {
      left: box.left,
      top: box.top,
      right: box.right,
      bottom: box.bottom,
      viewportWidth,
      viewportHeight,
    };
  });
  expect(hostBounds.left).toBeGreaterThanOrEqual(-1);
  expect(hostBounds.top).toBeGreaterThanOrEqual(-1);
  expect(hostBounds.right).toBeLessThanOrEqual(hostBounds.viewportWidth + 1);
  expect(hostBounds.bottom).toBeLessThanOrEqual(hostBounds.viewportHeight + 1);
  await page
    .getByRole("button", {
      name: "Add one point to Bartholomew With A Very Long Name",
    })
    .click();
  await page.getByRole("button", { name: "Review proposal (1)" }).click();
  await page.getByRole("button", { name: "Propose changes" }).click();
  await expect
    .poll(() =>
      room.clientMessages.find((message) => message.type === "admin_propose"),
    )
    .toMatchObject({
      actions: [{ kind: "set_score", player_id: "p2", score: -9 }],
    });

  await page.getByRole("button", { name: /Blank/ }).click();
  await page
    .getByRole("button", { name: "Fill in & play", exact: true })
    .click();
  await expectAuthoringFits(page);

  if (compact) {
    await page.getByTitle("Choose ink color").click();
    await expectPaletteFits(page, "[data-card-ink-palette]");
    await page
      .locator("[data-card-ink-palette]")
      .getByRole("button", { name: "Red" })
      .click();

    await page.getByTitle("Choose nib size").click();
    await expectPaletteFits(page, "[data-card-nib-palette]");
    await page
      .locator("[data-card-nib-palette]")
      .getByRole("button", { name: "9px nib" })
      .click();

    await page.getByTitle("Choose a stamp").click();
    await expectPaletteFits(page, "[data-card-stamp-palette]");
    expect(
      await page
        .locator("[data-card-stamp-palette]")
        .getByRole("button")
        .count(),
    ).toBe(15);
    await page
      .locator("[data-card-stamp-palette]")
      .getByRole("button", { name: "Stamp 🐱" })
      .click();
  }

  await page.getByLabel("Card title").fill("Pocket Volcano");
  await page.getByLabel("Card rules").fill("Everyone loses one point.");
  const canvas = page.getByLabel("Card drawing canvas");
  const ratio = await canvas.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return box.width / box.height;
  });
  expect(ratio).toBeCloseTo(6 / 5, 1);
  // A tap applies the armed stamp on compact screens (or a one-point stroke on
  // desktop) and exercises the keyboard-blur path without scrolling the dialog.
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
    await expectAuthoringFits(page);
    await page.setViewportSize(portrait);
    await expectAuthoringFits(page);
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

test("wide game panels share the viewport and preserve edits across the breakpoint", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");
  await openMockRoom(page);

  await expect(
    page.locator("[data-game-scroll]").getByText("Play Log"),
  ).toHaveCount(0);
  const shell = page.locator("[data-game-panel-shell]");
  await page.getByRole("button", { name: "Gallery" }).click();
  await expect(shell).toHaveAttribute("data-presentation", "sidebar");
  await expect(
    page.getByRole("complementary", { name: "The Deck" }),
  ).toBeVisible();
  await expect(page.getByTestId("gallery-scrim")).toHaveCount(0);

  await page.getByRole("button", { name: "Scores" }).click();
  await expect(shell).toHaveAttribute("data-presentation", "sidebar");
  await expect(
    page.getByRole("complementary", { name: "Scoreboard" }),
  ).toBeVisible();
  await expect(page.getByTestId("scoreboard-scrim")).toHaveCount(0);

  await page.getByRole("button", { name: "History" }).click();
  const game = page.locator("[data-game-scroll]");
  const panelBody = page.locator("[data-game-panel-body]");
  await expect(shell).toHaveAttribute("data-presentation", "sidebar");
  await expect(
    page.getByRole("complementary", { name: "Play Log" }),
  ).toBeVisible();
  await expect(page.getByText("Hand card 14")).toBeAttached();

  const geometry = await page.evaluate(() => {
    const game = document
      .querySelector<HTMLElement>("[data-game-scroll]")!
      .getBoundingClientRect();
    const panel = document
      .querySelector<HTMLElement>("[data-game-panel-shell]")!
      .getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      gameRight: game.right,
      panelLeft: panel.left,
      panelRight: panel.right,
      documentWidth: document.documentElement.scrollWidth,
    };
  });
  expect(Math.abs(geometry.gameRight - geometry.panelLeft)).toBeLessThanOrEqual(
    1,
  );
  expect(geometry.panelRight).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  expect(geometry.documentWidth).toBeLessThanOrEqual(
    geometry.viewportWidth + 1,
  );

  const initialGameScroll = await game.evaluate((node) => node.scrollTop);
  await panelBody.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  expect(await panelBody.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  expect(await game.evaluate((node) => node.scrollTop)).toBe(initialGameScroll);

  await page.getByRole("button", { name: "Host" }).click();
  await page
    .getByRole("button", {
      name: "Add one point to Bartholomew With A Very Long Name",
    })
    .click();
  await expect(
    page.getByLabel("Bartholomew With A Very Long Name score"),
  ).toHaveValue("-9");

  await page.setViewportSize({ width: 1279, height: 720 });
  await expect(shell).toHaveAttribute("data-presentation", "modal");
  await expect(
    page.getByRole("dialog", { name: "Host controls" }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Bartholomew With A Very Long Name score"),
  ).toHaveValue("-9");

  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(shell).toHaveAttribute("data-presentation", "sidebar");
  await expect(
    page.getByRole("complementary", { name: "Host controls" }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Bartholomew With A Very Long Name score"),
  ).toHaveValue("-9");
});

test("host can add a condition by its table-facing name", async ({ page }) => {
  const room = await openMockRoom(page);

  await page.getByRole("button", { name: "Host" }).click();
  await page.getByRole("button", { name: "Conditions" }).click();
  await expect(
    page.getByText("Enter any condition your table agreed to"),
  ).toBeVisible();
  await page.getByLabel("Condition name").fill("Speak Only in Questions");
  await expect(page.getByLabel("How should it be tracked?")).toHaveValue(
    "boolean",
  );
  await page.getByRole("button", { name: "Add condition change" }).click();
  await page.getByRole("button", { name: "Review proposal (1)" }).click();
  await page.getByRole("button", { name: "Propose changes" }).click();

  await expect
    .poll(() =>
      room.clientMessages.find((message) => message.type === "admin_propose"),
    )
    .toMatchObject({
      actions: [
        {
          kind: "set_condition",
          player_id: "p1",
          key: "speak_only_in_questions",
          value: true,
        },
      ],
    });
});

test("setup card authoring and preview keep the full card framed", async ({
  page,
}) => {
  const room = await openMockRoom(page, false, setupState());

  await page.getByRole("button", { name: "Author a card" }).click();
  await expectAuthoringFits(page);
  await page.getByLabel("Card title").fill("Pocket Umbrella");
  await page
    .getByLabel("Card rules")
    .fill("Prevent the next point loss this turn.");

  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect
    .poll(() =>
      room.clientMessages.find((message) => message.type === "preview_card"),
    )
    .toMatchObject({
      title: "Pocket Umbrella",
      description: "Prevent the next point loss this turn.",
    });

  room.push({
    type: "preview_result",
    verdict: "ok",
    mechanical_status: "applied",
    mechanical_reason:
      "The effect is executable and expires after the current turn.",
    correlation_id: "preview-mobile-1",
    program: "prevent_point_loss(actor); ".repeat(20),
  });
  await expect(page.locator("[data-preview-status]")).toContainText("applied");
  await expectAuthoringFits(page);

  await page.getByRole("button", { name: "Details" }).click();
  await expectPaletteFits(page, "[data-preview-details]");
  await expect(page.locator("[data-preview-details]")).toContainText(
    "preview-mobile-1",
  );
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect
    .poll(() =>
      room.clientMessages.find((message) => message.type === "create_card"),
    )
    .toMatchObject({
      title: "Pocket Umbrella",
      description: "Prevent the next point loss this turn.",
    });
});
