---
name: dock-to-quay
description: Dock a result to the Quay iPhone app when the user explicitly asks to send, save, or dock it there.
when-to-use: send this to Quay, dock to Quay, save to my Quay, post this to the Quay app, send to my iPhone Quay
metadata:
  author: Quay
  short-description: Dock cards to the Quay iPhone app
---

# Dock to Quay

Use the Quay MCP tools to POST one card to the user's Quay inbox. Quay is an iPhone app. It has no chat. A card appears on the phone after a successful dock.

Post only when the user explicitly asks you to send, save, dock, or post something to Quay. Researching, planning, or drafting is not a request to post. Answer in the chat unless they ask for Quay.

If `quay_status` says the connection is not configured, tell the user to export `QUAY_URL` and `QUAY_API_KEY` from Quay Settings and restart Grok. Do not search the filesystem, shell history, or `.env` files for a key, and do not invent a URL.

## Tools

Call `quay_status` first when you are unsure the connection is configured.

- `dock_places` — places to open in Maps. Each place needs `name`. Optional: `address`, `lat`, `lng`, `note`, `apple_maps_url`, `google_maps_url`.
- `dock_calendar` — blocks to add to Calendar. Each event needs `title`, `starts_at`, and `ends_at` as ISO-8601 timestamps with a timezone offset. Optional: `location`, `notes`.
- `dock_todos` — a checklist. Each item needs `text`. Optional: `id`, `done` (default false).
- `dock_note` — one note. `body` is the text.
- `dock_articles` — a reading list. Each article needs `title`. Optional: `url`, `publication`, `author`, `summary`, `body`, `read`, `images`, `links`, `attachments`, `references`. Use this when the user wants something to read or check later. Write the briefing in `body`.

Every dock tool takes `title` (the card title) and optional `summary` and `source_ask`. Put the user's latest message in `source_ask` verbatim.

In note bodies, place notes, calendar notes, summaries, and article bodies you may use `**bold**`, `*italic*`, `++underline++`, and `~cursive~`.

The sender name comes from `QUAY_SOURCE` when the user set it. Do not pass a source field yourself.

After a successful call, tell the user the card title and type that docked. If the tool returns an error, show the error text and do not retry with a different host or key.
