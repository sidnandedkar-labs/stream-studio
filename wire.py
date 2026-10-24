"""Minimal streaming chat transport. Standard library only; never logs credentials."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class StreamError(Exception):
    pass


def endpoint_url(base):
    parsed = urllib.parse.urlsplit(base)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("Use HTTPS, or HTTP on localhost for a mock server")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL must not include credentials, query, or fragment")
    return base.rstrip("/") + "/chat/completions"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Avoid forwarding an Authorization header to an unexpected destination."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise StreamError(f"Upstream redirect refused (HTTP {code})")


def sse_events(lines):
    """Decode SSE data fields, including multi-line events and CRLF."""
    data = []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n") if isinstance(raw, bytes) else raw.rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield "\n".join(data)


def stream(base, key, model, messages, max_tokens=512, timeout=60, include_usage=True):
    payload = {"model": model, "messages": messages, "stream": True,
               "max_tokens": max_tokens}
    if include_usage:
        payload["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(endpoint_url(base), data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json",
                                          "Accept": "text/event-stream"})
    opener = urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic() + timeout
    saw_finish = False
    try:
        with opener.open(req, timeout=timeout) as response:
            for data in sse_events(response):
                if time.monotonic() > deadline:
                    raise StreamError("Stream exceeded its elapsed-time limit")
                if data == "[DONE]":
                    return
                try:
                    event = json.loads(data)
                except (ValueError, UnicodeError) as exc:
                    raise StreamError("Invalid JSON in SSE response") from exc
                if not isinstance(event, dict):
                    raise StreamError("Expected an object in the event stream")
                if event.get("error"):
                    raise StreamError("Provider returned a streaming error; see provider console")
                for choice in event.get("choices") or []:
                    if choice.get("finish_reason") is not None:
                        saw_finish = True
                yield event
            if not saw_finish:
                raise StreamError("Stream ended before a finish event or [DONE]")
    except urllib.error.HTTPError as exc:
        # Provider bodies may echo user text or secrets. Don't expose them by default.
        exc.close()
        raise StreamError(f"Provider HTTP {exc.code}; check endpoint, model, access and quota") from exc
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as exc:
        raise StreamError(f"Transport failed ({type(exc).__name__})") from exc


def content_parts(event):
    for choice in event.get("choices") or []:
        if choice.get("index", 0) != 0:
            continue
        delta = choice.get("delta") or {}
        for kind in ("reasoning_content", "content"):
            if isinstance(delta.get(kind), str) and delta[kind]:
                yield kind, delta[kind]


def demo_events(text="This is a synthetic local stream. Connect a provider to measure a real model."):
    """Deterministic content; timing is deliberately simulated, not provider data."""
    yield {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
    for word in text.split(" "):
        time.sleep(0.025)
        yield {"choices": [{"index": 0, "delta": {"content": word + " "}}]}
    yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    # No tokenizer is used: do not invent completion-token usage for the demo.


def measure(events, emit=None):
    start = time.perf_counter()
    first = first_content = None
    chunks = chars = reasoning_chars = 0
    usage = None
    finish = None
    for event in events:
        now = time.perf_counter()
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            if choice.get("index", 0) == 0 and choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
        for kind, text in content_parts(event):
            first = now if first is None else first
            if kind == "content":
                first_content = now if first_content is None else first_content
                chars += len(text)
            else:
                reasoning_chars += len(text)
            chunks += 1
            if emit:
                emit({"type": kind, "text": text, "elapsed_s": now - start})
    elapsed = time.perf_counter() - start
    tokens = (usage or {}).get("completion_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, (int, float)) or tokens < 0:
        tokens = None
    return {"elapsed_s": elapsed, "ttft_s": None if first is None else first - start,
            "first_content_s": None if first_content is None else first_content - start,
            "completion_tokens": tokens, "usage": usage, "finish_reason": finish,
            "end_to_end_tps": tokens / elapsed if tokens is not None and elapsed > 0 else None,
            "content_chars": chars, "reasoning_chars": reasoning_chars, "text_chunks": chunks}
