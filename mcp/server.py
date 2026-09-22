#!/usr/bin/env python3
"""Stdio MCP server that docks one card to a user's Quay inbox.

Talks only to https://<user-host>/functions/v1/ingest. The API key is read
from QUAY_API_KEY and sent as a Bearer token to that URL. Nothing else is
read from disk or from the environment.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

PROTOCOL = "2024-11-05"
SERVER_NAME = "quay"
SERVER_VERSION = "1.0.0"
INGEST_PATH = "/functions/v1/ingest"
MAX_MESSAGE_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 65_536
REQUEST_TIMEOUT_SEC = 20
BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.com",
}

Resolver = Callable[[str, int, int], list]


class QuayError(Exception):
    """A user-facing configuration or validation error. Never includes the API key."""


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or "${" in value:
        return ""
    return value


def _source_name() -> str:
    raw = _env("QUAY_SOURCE") or "Grok"
    cleaned = raw.replace("\\", "").replace('"', "").strip()
    return cleaned[:40] or "Grok"


def _reject_non_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if not ip.is_global:
        raise QuayError("QUAY_URL must resolve to a public address")


def assert_public_host(host: str, resolver: Resolver = socket.getaddrinfo) -> None:
    lowered = host.lower().rstrip(".")
    if lowered in BLOCKED_HOSTS or lowered.endswith(".localhost") or lowered.endswith(".local"):
        raise QuayError("QUAY_URL must be a public https host")
    try:
        literal = ipaddress.ip_address(lowered)
    except ValueError:
        literal = None
    if literal is not None:
        _reject_non_public_ip(literal)
        return
    try:
        infos = resolver(lowered, 443, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise QuayError(f"Could not resolve QUAY_URL host: {exc}") from exc
    if not infos:
        raise QuayError("Could not resolve QUAY_URL host")
    for info in infos:
        _reject_non_public_ip(ipaddress.ip_address(info[4][0]))


def ingest_url(raw: str, resolver: Resolver = socket.getaddrinfo) -> str:
    if not raw:
        raise QuayError(
            "Set QUAY_URL to your Quay project URL, for example https://YOUR_PROJECT.supabase.co"
        )
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname:
        raise QuayError("QUAY_URL must be an https URL")
    if parts.username or parts.password:
        raise QuayError("QUAY_URL must not include a username or password")
    if parts.query or parts.fragment:
        raise QuayError("QUAY_URL must not include a query string or fragment")
    port = parts.port
    if port not in (None, 443):
        raise QuayError("QUAY_URL must use port 443")
    path = parts.path.rstrip("/")
    if path in ("",):
        path = INGEST_PATH
    elif path != INGEST_PATH:
        raise QuayError("QUAY_URL path must be /functions/v1/ingest or omitted")
    assert_public_host(parts.hostname, resolver)
    return f"https://{parts.hostname}{path}"


def api_key() -> str:
    key = _env("QUAY_API_KEY")
    if not key:
        raise QuayError(
            "Set QUAY_API_KEY to the quay_ key from the Quay app (Settings). Restart Grok after exporting it."
        )
    return key


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QuayError(f"{field} must be a string")
    trimmed = value.strip()
    return trimmed or None


def _required_str(value: Any, field: str) -> str:
    text = _optional_str(value, field)
    if not text:
        raise QuayError(f"{field} is required")
    return text


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QuayError(f"{field} must be a number")
    return float(value)


def _http_url(value: Any, field: str, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise QuayError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise QuayError(f"{field} must be a string")
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise QuayError(f"{field} must be an http(s) URL")
    return value.strip()


def _iso8601(value: Any, field: str) -> str:
    text = _required_str(value, field)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise QuayError(f"{field} must be ISO-8601") from exc
    return text


def _objects(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise QuayError(f"{field} must be a non-empty array")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise QuayError(f"{field}[{index}] must be an object")
        items.append(item)
    return items


def _card(card_type: str, args: dict[str, Any], payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": card_type,
        "title": _required_str(args.get("title"), "title")[:200],
        "source": _source_name(),
        "payload": payload,
    }
    summary = _optional_str(args.get("summary"), "summary")
    if summary:
        body["summary"] = summary
    source_ask = _optional_str(args.get("source_ask"), "source_ask")
    if source_ask:
        body["source_ask"] = source_ask
    return body


def places_payload(raw: Any) -> list[dict[str, Any]]:
    items = []
    for index, place in enumerate(_objects(raw, "places")):
        items.append(
            {
                "name": _required_str(place.get("name"), f"places[{index}].name"),
                "address": _optional_str(place.get("address"), f"places[{index}].address"),
                "lat": _optional_number(place.get("lat"), f"places[{index}].lat"),
                "lng": _optional_number(place.get("lng"), f"places[{index}].lng"),
                "note": _optional_str(place.get("note"), f"places[{index}].note"),
                "apple_maps_url": _http_url(place.get("apple_maps_url"), f"places[{index}].apple_maps_url"),
                "google_maps_url": _http_url(place.get("google_maps_url"), f"places[{index}].google_maps_url"),
            }
        )
    return items


def calendar_payload(raw: Any) -> list[dict[str, Any]]:
    items = []
    for index, event in enumerate(_objects(raw, "events")):
        items.append(
            {
                "title": _required_str(event.get("title"), f"events[{index}].title"),
                "starts_at": _iso8601(event.get("starts_at"), f"events[{index}].starts_at"),
                "ends_at": _iso8601(event.get("ends_at"), f"events[{index}].ends_at"),
                "location": _optional_str(event.get("location"), f"events[{index}].location"),
                "notes": _optional_str(event.get("notes"), f"events[{index}].notes"),
            }
        )
    return items


def todos_payload(raw: Any) -> list[dict[str, Any]]:
    items = []
    for index, item in enumerate(_objects(raw, "items")):
        done = item.get("done", False)
        if not isinstance(done, bool):
            raise QuayError(f"items[{index}].done must be a boolean")
        entry: dict[str, Any] = {
            "text": _required_str(item.get("text"), f"items[{index}].text"),
            "done": done,
        }
        item_id = _optional_str(item.get("id"), f"items[{index}].id")
        if item_id:
            entry["id"] = item_id
        items.append(entry)
    return items


def note_payload(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"body": None}
    if not isinstance(raw, str):
        raise QuayError("body must be a string")
    return {"body": raw}


def _media_list(raw: Any, field: str, require_title: bool) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise QuayError(f"{field} must be an array")
    items = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise QuayError(f"{field}[{index}] must be an object")
        url = _http_url(item.get("url"), f"{field}[{index}].url", required=field != "references")
        title = _optional_str(item.get("title"), f"{field}[{index}].title")
        if require_title and not title:
            raise QuayError(f"{field}[{index}].title is required")
        entry = {"url": url} if url else {}
        if title:
            entry["title"] = title
        for key in ("alt", "caption", "note", "kind", "citation", "publication"):
            text = _optional_str(item.get(key), f"{field}[{index}].{key}")
            if text:
                entry[key] = text
        items.append(entry)
    return items


def articles_payload(raw: Any) -> list[dict[str, Any]]:
    items = []
    for index, article in enumerate(_objects(raw, "articles")):
        prefix = f"articles[{index}]"
        read = article.get("read", False)
        if not isinstance(read, bool):
            raise QuayError(f"{prefix}.read must be a boolean")
        entry: dict[str, Any] = {
            "title": _required_str(article.get("title"), f"{prefix}.title"),
            "read": read,
        }
        for key in ("url",):
            url = _http_url(article.get(key), f"{prefix}.{key}")
            if url:
                entry[key] = url
        for key in ("publication", "author", "summary", "body", "id"):
            text = _optional_str(article.get(key), f"{prefix}.{key}")
            if text:
                entry[key] = text
        for key, titled in (
            ("images", False),
            ("links", True),
            ("attachments", True),
            ("references", True),
        ):
            media = _media_list(article.get(key), f"{prefix}.{key}", require_title=titled)
            if media:
                entry[key] = media
        items.append(entry)
    return items


def build_card(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "dock_places":
        return _card("places", args, places_payload(args.get("places")))
    if name == "dock_calendar":
        return _card("calendar", args, calendar_payload(args.get("events")))
    if name == "dock_todos":
        return _card("todos", args, todos_payload(args.get("items")))
    if name == "dock_note":
        return _card("note", args, note_payload(args.get("body")))
    if name == "dock_articles":
        return _card("articles", args, articles_payload(args.get("articles")))
    raise QuayError(f"Unknown tool {name}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise QuayError(f"Quay ingest refused a redirect ({code})")


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def http_json(
    method: str,
    url: str,
    key: str,
    body: dict[str, Any] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"quay-grok-plugin/{SERVER_VERSION}",
        },
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    client = opener or _opener()
    try:
        with client.open(request, timeout=REQUEST_TIMEOUT_SEC) as response:
            status = getattr(response, "status", 200)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except QuayError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        finally:
            exc.close()
        try:
            parsed = json.loads(detail)
            message = parsed.get("detail") or parsed.get("error") or detail
        except json.JSONDecodeError:
            message = detail.strip() or exc.reason
        raise QuayError(f"Quay ingest returned HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise QuayError(f"Could not reach Quay ingest: {exc.reason}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise QuayError("Quay ingest response was too large")
    if not raw:
        return status, {}
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise QuayError("Quay ingest returned non-JSON") from exc


def status_report(
    resolver: Resolver = socket.getaddrinfo,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    url = ingest_url(_env("QUAY_URL"), resolver)
    key = api_key()
    _status, contract = http_json("GET", url, key, opener=opener)
    host = urlsplit(url).hostname
    return {
        "configured": True,
        "host": host,
        "source": _source_name(),
        "key_present": True,
        "contract": contract.get("name") if isinstance(contract, dict) else None,
        "types": contract.get("types") if isinstance(contract, dict) else None,
    }


def dock(
    name: str,
    args: dict[str, Any],
    resolver: Resolver = socket.getaddrinfo,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    url = ingest_url(_env("QUAY_URL"), resolver)
    key = api_key()
    card = build_card(name, args)
    status, payload = http_json("POST", url, key, card, opener=opener)
    item = payload.get("item") if isinstance(payload, dict) else None
    return {"ok": True, "status": status, "item": item}


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "quay_status",
        "description": "Check the Quay connection. Returns the ingest host and card types, never the API key.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "dock_places",
        "description": "Dock a places card to the user's Quay inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["title", "places"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_ask": {"type": "string"},
                "places": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "address": {"type": "string"},
                            "lat": {"type": "number"},
                            "lng": {"type": "number"},
                            "note": {"type": "string"},
                            "apple_maps_url": {"type": "string"},
                            "google_maps_url": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "dock_calendar",
        "description": "Dock a calendar card to the user's Quay inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["title", "events"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_ask": {"type": "string"},
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title", "starts_at", "ends_at"],
                        "properties": {
                            "title": {"type": "string"},
                            "starts_at": {"type": "string"},
                            "ends_at": {"type": "string"},
                            "location": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "dock_todos",
        "description": "Dock a todos card to the user's Quay inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["title", "items"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_ask": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["text"],
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "done": {"type": "boolean"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "dock_note",
        "description": "Dock a note card to the user's Quay inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_ask": {"type": "string"},
                "body": {"type": "string"},
            },
        },
    },
    {
        "name": "dock_articles",
        "description": "Dock a reading-list card to the user's Quay inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["title", "articles"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source_ask": {"type": "string"},
                "articles": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
]


def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    resolver: Resolver = socket.getaddrinfo,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    args = arguments or {}
    if name == "quay_status":
        result = status_report(resolver, opener)
    elif name.startswith("dock_"):
        result = dock(name, args, resolver, opener)
    else:
        raise QuayError(f"Unknown tool {name}")
    return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}


def handle_message(
    message: dict[str, Any],
    resolver: Resolver = socket.getaddrinfo,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method in ("notifications/initialized", "initialized") or msg_id is None and method:
        if msg_id is None:
            return None
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOL_DEFS}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if not isinstance(name, str):
                raise QuayError("tools/call requires a tool name")
            try:
                return _result(msg_id, call_tool(name, arguments, resolver, opener))
            except QuayError as exc:
                return _result(
                    msg_id,
                    {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                )
        else:
            return _error(msg_id, -32601, f"Method not found: {method}")
    except QuayError as exc:
        return _error(msg_id, -32000, str(exc))
    return _result(msg_id, result)


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        headers[key.decode("ascii", errors="replace").strip().lower()] = value.decode(
            "ascii", errors="replace"
        ).strip()
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError as exc:
        raise QuayError("Invalid Content-Length") from exc
    if length <= 0 or length > MAX_MESSAGE_BYTES:
        raise QuayError("Invalid Content-Length")
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise QuayError("Invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise QuayError("Invalid JSON")
    return parsed


def write_message(message: dict[str, Any]) -> None:
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main() -> int:
    while True:
        try:
            message = read_message()
        except QuayError as exc:
            write_message(_error(None, -32700, str(exc)))
            continue
        if message is None:
            return 0
        try:
            response = handle_message(message)
        except QuayError as exc:
            response = _error(message.get("id"), -32000, str(exc))
        if response is not None:
            write_message(response)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
