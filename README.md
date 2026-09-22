# Quay plugin for Grok Build

This plugin connects [Grok Build](https://x.ai/build) to [Quay](https://github.com/Ataguan/quay), the iPhone app where agent results dock. There is no chat UI in Quay. When you ask Grok to send something to Quay, the bundled MCP server POSTs one card to your personal ingest endpoint. Cards show up on the iPhone as places, calendar blocks, todos, notes, or reading lists.

Quay is not affiliated with xAI.

## What it calls

The server talks to exactly one URL, which you configure:

`https://YOUR_PROJECT.supabase.co/functions/v1/ingest`

- `GET` reads the public card contract.
- `POST` docks one card.

It sends `Authorization: Bearer` with your Quay API key and nothing else. It does not read files, shell history, or other environment variables. It refuses non-HTTPS URLs, redirects, query strings, and hosts that are not public.

## Credentials

From the Quay iPhone app, after sign-in, open **Settings** and copy the project URL and the `quay_…` API key. Then, before you start Grok:

```bash
export QUAY_URL="https://YOUR_PROJECT.supabase.co"
export QUAY_API_KEY="quay_…"
export QUAY_SOURCE="Scout"   # optional name shown on each card
```

`QUAY_URL` may be the project origin or the full ingest URL. The key is sent only to that project's `/functions/v1/ingest` path.

## Install

After this plugin is in the Grok Build marketplace:

```bash
grok plugin install quay --trust
```

Until then, install this directory from the Quay repo:

```bash
grok plugin install /path/to/quay/grok-plugin --trust
```

Python 3.9+ must already be on `PATH` (`python3`). The server uses the standard library only.

Restart Grok after exporting the variables so the MCP process inherits them. Ask Grok to dock a result, for example: “Send a Saturday in Lisbon to Quay.” Grok should call the Quay tools and should not post unless you asked it to.

## Tools

| Tool | Card |
| --- | --- |
| `quay_status` | Checks that the URL and key are set, then GETs the contract. Never returns the key. |
| `dock_places` | Places with name, address, coordinates, and note |
| `dock_calendar` | Calendar blocks with ISO-8601 start and end |
| `dock_todos` | Checklist items |
| `dock_note` | A note body |
| `dock_articles` | A reading list with Markdown body, images, links, attachments, and references |

## License

MIT. See `LICENSE`.
