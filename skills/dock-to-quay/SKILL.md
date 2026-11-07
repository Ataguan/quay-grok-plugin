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

If `quay_status` says the connection is not configured, tell the user to set `QUAY_URL` and `QUAY_API_KEY` from Quay Settings → Copy Connection (or API key) and restart Grok. Do not search the filesystem, shell history, or `.env` files for a key, and do not invent a URL.

## Live instructions

Call `get_contract` (or rely on `quay_status`) at the start of a session. Quay hosts the posting guide and `ui.catalog` on the ingest GET. Prefer that over any pasted catalog — formats change without the user re-pasting a prompt.

## Tools

Call `quay_status` first when you are unsure the connection is configured.

- `get_contract` — live instructions, `instructions_version`, and `ui.catalog`.
- `dock_ui` — **default**. Compose a layout tree: `stack`, `split`, `text`, `markdown`, `list`, `item`, `todo_list`, `image`, `map`, `badge`, `board`, `column`. Pass `root` as the root node.
- `dock_places` — simple places card (still supported).
- `dock_calendar` — calendar blocks (still supported).
- `dock_todos` — checklist card (prefer `todo_list` inside `dock_ui` when composing).
- `dock_note` — one note (still supported).
- `dock_articles` — reading list (still supported).

Use specialty HTTP types `quiz`, `recipe`, or `memory` only when that interaction is clearly best (via curl/ingest if no MCP tool yet).

Every dock tool takes `title` (the card title) and optional `summary` and `source_ask`. Put the user's latest message in `source_ask` verbatim.

The sender name comes from `QUAY_SOURCE` when the user set it. Do not pass a source field yourself.

After a successful call, tell the user the card title and type that docked. If the tool returns an error, show the error text and do not retry with a different host or key.
