"""Shared machinery for the Playwright end-to-end harness.

Drives the live app at http://127.0.0.1:3000 through a real Chromium browser,
exactly like an operator would. Every test captures browser console messages,
page errors, failed HTTP requests, HTTP >= 400 responses, and the full
socket.io event stream (both directions) as JSON artifacts under
tests/e2e/artifacts/.

No fixed sleeps are used for synchronisation anywhere in this harness: all
waiting is done with Playwright web-first assertions or in-page
`wait_for_function` predicates with explicit timeouts.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

# --- Configuration -----------------------------------------------------------

BASE_URL = os.environ.get("REGOLITH_E2E_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
HEADED = os.environ.get("REGOLITH_E2E_HEADED", "") == "1"

# Origins the app genuinely references (controller-verified 2026-08-28): the
# three CDN dependencies in web/templates/base.html. A failure of one of THESE
# is a real finding; a failure of any other third-party origin is browser/
# environment noise and is recorded separately, never reported as a defect.
APP_CDN_PREFIXES = (
    "https://cdn.plot.ly/plotly-",
    "https://cdn.socket.io/",
    "https://unpkg.com/htmx.org",
)

E2E_DIR = Path(__file__).resolve().parent
ARTIFACTS_ROOT = E2E_DIR / "artifacts"

# Bounded waits (milliseconds). Baselines measured by the controller:
# GET / ~1.1 s, GET /api/runs ~1.2 s, GET /thermal-train ~0.002 s,
# GET /optimizer ~7 MINUTES (known live defect, fix in flight).
from .journey_budget import (  # noqa: F401 -- re-exported for existing importers
    FEEDSTOCK_CARD_MS,
    JOURNEY_BUDGET_MS,
    JOURNEY_MARGIN_MS,
    JOURNEY_TIMEOUT_S,
    OPTIMIZER_BOUND_MS,
    PAGE_LOAD_MS,
    RUN_COMPLETE_MS,
    RUN_COMPLETE_TOTAL_MS,
    SOCKET_CONNECT_MS,
    STALL_THRESHOLD_MS,
    START_ACK_MS,
    STATUS_CHANGE_MS,
    THERMAL_TRAIN_MS,
    TICK_ADVANCE_MS,
    WATCHDOG_WINDOW_MS,
)

DEFAULT_FEEDSTOCK = "lunar_mare_low_ti"

# --- In-page instrumentation (installed via add_init_script) -----------------

# Taps the socket.io client: records every inbound event (onAny) and every
# outbound emit into window.__e2eSocketLog. Works by intercepting the global
# `io` assignment made by the socket.io UMD bundle loaded from the CDN, then
# wrapping the socket instance before the app code receives it. Defensive by
# construction: if anything throws, the app gets the unwrapped factory.
SOCKET_TAP_JS = r"""
(() => {
    if (window.__e2eSocketTapInstalled) return;
    window.__e2eSocketTapInstalled = true;
    window.__e2eSocketLog = [];
    let realIo = null;
    // Tick payloads are megabytes; keep them out of the in-page log. Always
    // keep start/status/complete/decision events whole (those are the
    // operator-visible control plane) and keep a verbatim excerpt around
    // any out_of_domain token so a stall's payload is the deliverable.
    const KEEP_FULL_EVENT = /^(start_simulation|simulation_status|simulation_complete|decision_required|make_decision|__tap_)/;
    const KEY_RE = /^(status|hour|campaign|message|reason|detail|run_id|knudsen|backend|temperature|temp_|yield|error|disposition|ledger_yields|affected_species|overhead|pipe_|carrier|stage|phase)/i;
    const compactValue = (value) => {
        let blob;
        try { blob = JSON.stringify(value ?? null); }
        catch (e) { return { _unserialisable: String(e) }; }
        if (blob.length <= 8000) {
            try { return JSON.parse(blob); } catch (e) { return blob.slice(0, 8000); }
        }
        const keep = { _truncated: true, data_bytes: blob.length };
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            for (const k of Object.keys(value)) {
                if (!KEY_RE.test(k) && !/out_of_domain/i.test(k)) continue;
                try {
                    const piece = JSON.stringify(value[k]);
                    keep[k] = piece.length <= 4000 ? JSON.parse(piece) : piece.slice(0, 4000);
                } catch (e) {
                    keep[k] = String(value[k]).slice(0, 400);
                }
            }
        }
        const idx = blob.search(/out_of_domain/i);
        if (idx >= 0) {
            keep._out_of_domain_excerpt = blob.slice(Math.max(0, idx - 240), Math.min(blob.length, idx + 2000));
        }
        keep._preview = blob.slice(0, 300);
        return keep;
    };
    const record = (entry) => {
        try { window.__e2eSocketLog.push(entry); } catch (e) { /* never break the app */ }
    };
    const compactFor = (event, value) => {
        if (KEEP_FULL_EVENT.test(event || '')) {
            try { return JSON.parse(JSON.stringify(value ?? null)); }
            catch (e) { return compactValue(value); }
        }
        return compactValue(value);
    };
    const hookSocket = (socket) => {
        if (!socket || socket.__e2eHooked) return socket;
        try {
            socket.__e2eHooked = true;
            if (typeof socket.onAny === 'function') {
                socket.onAny((event, ...args) => {
                    record({
                        ms: Date.now(),
                        dir: 'in',
                        event: event,
                        data: compactFor(event, args.length === 1 ? args[0] : args),
                    });
                });
            } else {
                record({ ms: Date.now(), dir: 'meta', event: '__tap_warning', data: 'socket.onAny missing' });
            }
            const origEmit = socket.emit.bind(socket);
            socket.emit = (event, ...args) => {
                record({
                    ms: Date.now(),
                    dir: 'out',
                    event: event,
                    data: compactFor(event, args.length === 1 ? args[0] : args),
                });
                return origEmit(event, ...args);
            };
        } catch (e) {
            record({ ms: Date.now(), dir: 'meta', event: '__tap_error', data: String(e) });
        }
        return socket;
    };
    Object.defineProperty(window, 'io', {
        configurable: true,
        enumerable: true,
        get() {
            if (!realIo) return realIo;
            const wrapped = function (...args) { return hookSocket(realIo(...args)); };
            try { Object.assign(wrapped, realIo); } catch (e) { /* best effort */ }
            if (realIo && realIo.connect) {
                try { wrapped.connect = (...a) => hookSocket(realIo.connect(...a)); } catch (e) { /* best effort */ }
            }
            return wrapped;
        },
        set(value) { realIo = value; },
    });
})();
"""

# Auto-answers the app's decision modal the way an operator following the
# recommendation would: clicks the `.btn-primary` (recommended) option and
# records the modal text in window.__e2eDecisions. Event-driven
# (MutationObserver), so a run can never silently stall waiting on a modal the
# harness refused to answer.
DECISION_AUTO_ANSWER_JS = r"""
(() => {
    if (window.__e2eDecisionHookInstalled) return;
    window.__e2eDecisionHookInstalled = true;
    window.__e2eDecisions = [];
    const handle = () => {
        const modal = document.getElementById('decision-modal');
        if (!modal || modal.__e2eHandled) return;
        modal.__e2eHandled = true;
        let text = '';
        try { text = modal.innerText.slice(0, 800); } catch (e) { /* ignore */ }
        const recommended = modal.querySelector('.btn-primary') || modal.querySelector('.btn');
        window.__e2eDecisions.push({ ms: Date.now(), text: text, answered: !!(recommended) });
        if (recommended) recommended.click();
    };
    const observer = new MutationObserver(handle);
    try { observer.observe(document, { childList: true, subtree: true }); } catch (e) { /* ignore */ }
})();
"""

# Predicate used by wait_for_function while a run is live. Returns false while
# nothing changed; returns a tagged string as soon as the run ADVANCES past
# `lastHour`, reaches a terminal/refused/error status, or completes. The
# status text format is `${status} — ${message}` (simulator-socket.js), and
# `#status-hour` renders `Hour: <n>` (simulator-ticks.js).
RUN_STATE_PREDICATE_JS = r"""
(lastHour) => {
    const statusEl = document.getElementById('status-text');
    const status = statusEl ? (statusEl.textContent || '').trim() : '';
    if (/^Complete\b/.test(status)) return 'COMPLETE::' + status;
    if (/^refused\b/i.test(status)) return 'REFUSED::' + status;
    if (/^error\b/i.test(status)) return 'ERROR::' + status;
    const hourEl = document.getElementById('status-hour');
    const m = hourEl ? (hourEl.textContent || '').match(/Hour:\s*([0-9]+(?:\.[0-9]+)?)/) : null;
    if (m && parseFloat(m[1]) > lastHour) return 'ADVANCED::' + m[1] + '::' + status;
    return false;
}
"""

# Predicate used to wait for a specific inbound socket.io event inside the
# tapped log. Returns the event's data as a JSON string, or false while no
# matching event has arrived.
SOCKET_EVENT_PREDICATE_JS = r"""
(spec) => {
    const log = window.__e2eSocketLog || [];
    for (const e of log) {
        if (e.dir !== 'in' || e.event !== spec.event) continue;
        if (!spec.statuses || spec.statuses.length === 0) return JSON.stringify(e.data);
        if (e.data && spec.statuses.includes(e.data.status)) return JSON.stringify(e.data);
    }
    return false;
}
"""

# --- Evidence capture --------------------------------------------------------


class EvidenceRecorder:
    """Captures console messages, page errors, network failures, HTTP >= 400
    responses, and the tapped socket.io stream for one test."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.console: list[dict[str, Any]] = []
        self.page_errors: list[str] = []
        self.request_failures: list[dict[str, Any]] = []
        self.third_party_failures: list[dict[str, Any]] = []
        self.http_errors: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.socket_events: list[dict[str, Any]] = []
        self.dialogs: list[str] = []
        self.dialog_seen = threading.Event()
        self.notes: list[str] = []

        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("requestfailed", self._on_requestfailed)
        page.on("response", self._on_response)
        # Dialog safety net: an unhandled native dialog blocks page.evaluate
        # and context.close() forever (it deadlocked the first harness run).
        # Record + dismiss every dialog, immediately, in every test.
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog) -> None:
        try:
            self.dialogs.append(dialog.message)
        except Exception:
            self.dialogs.append("<unreadable>")
        self.dialog_seen.set()
        try:
            dialog.dismiss()
        except Exception:
            pass

    def _on_console(self, msg) -> None:
        entry = {"type": msg.type, "text": msg.text[:1000]}
        try:
            loc = msg.location
            if loc:
                entry["location"] = f"{loc.get('url', '')}:{loc.get('lineNumber', '')}"
        except Exception:
            pass
        self.console.append(entry)

    def _on_pageerror(self, error) -> None:
        self.page_errors.append(str(error)[:2000])

    def _on_requestfailed(self, request) -> None:
        failure = ""
        try:
            failure = request.failure or ""
        except Exception:
            pass
        url = request.url
        app_origin = url.startswith(BASE_URL) or url.startswith(APP_CDN_PREFIXES)
        # Aborts caused by our own navigation are expected journey noise: the
        # browser cancels in-flight long-polls and htmx partial GETs when we
        # leave a page. Cross-origin or non-abort failures stay loud.
        benign = "ERR_ABORTED" in failure and (
            "/socket.io/" in url or (url.startswith(BASE_URL) and request.method == "GET")
        )
        entry = {
            "url": url[:500],
            "method": request.method,
            "failure": failure,
            "benign_socketio_abort": benign,
        }
        if app_origin:
            self.request_failures.append(entry)
        else:
            # Browser-environment noise (extensions, devtools, stray profile
            # state) — recorded for completeness, never reported as a defect.
            self.third_party_failures.append(entry)

    def _on_response(self, response) -> None:
        try:
            status = response.status
        except Exception:
            return
        if status >= 400:
            url = response.url
            entry = {"url": url[:500], "status": status, "method": response.request.method}
            if url.startswith(BASE_URL) or url.startswith(APP_CDN_PREFIXES):
                self.http_errors.append(entry)
            else:
                self.third_party_failures.append(entry)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def harvest_socket_log(self, phase: str) -> None:
        """Pull window.__e2eSocketLog / __e2eDecisions out of the page.

        Must be called BEFORE any navigation away from the page being
        observed (the in-page log resets on navigation). Only new entries
        since the last harvest are appended (the offset resets when the
        in-page list shrinks, i.e. after a navigation)."""
        for key, target in (("__e2eSocketLog", self.socket_events), ("__e2eDecisions", self.decisions)):
            offset_key = f"_{key}_offset"
            offset = getattr(self, offset_key, 0)
            try:
                entries = self.page.evaluate(f"window.{key} || []")
            except Exception as exc:
                entries = []
                self.note(f"{key} harvest failed ({phase}): {exc}")
            if len(entries) < offset:
                offset = 0
            for entry in entries[offset:]:
                entry["phase"] = phase
            target.extend(entries[offset:])
            setattr(self, offset_key, len(entries))

    def socket_events_matching(self, pattern: str) -> list[dict[str, Any]]:
        rx = re.compile(pattern, re.IGNORECASE)
        out = []
        for event in self.socket_events:
            try:
                blob = json.dumps(event.get("data"))
            except Exception:
                blob = str(event.get("data"))
            if rx.search(event.get("event", "")) or rx.search(blob):
                out.append(event)
        return out

    def console_errors(self) -> list[dict[str, Any]]:
        return [e for e in self.console if e["type"] in ("error",)]

    def real_request_failures(self) -> list[dict[str, Any]]:
        return [f for f in self.request_failures if not f["benign_socketio_abort"]]

    def summary(self) -> str:
        lines = [
            f"console messages: {len(self.console)} (errors: {len(self.console_errors())})",
            f"page errors: {len(self.page_errors)}",
            f"request failures (app origins): {len(self.real_request_failures())}"
            f" (+{len(self.request_failures) - len(self.real_request_failures())} benign navigation aborts)",
            f"third-party/environment request noise (not app defects): {len(self.third_party_failures)}",
            f"HTTP >= 400 responses (app origins): {len(self.http_errors)}",
            f"socket.io events captured: {len(self.socket_events)}",
            f"decision modals auto-answered: {len(self.decisions)}",
        ]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_url": BASE_URL,
            "notes": self.notes,
            "dialogs": self.dialogs,
            "console": self.console,
            "page_errors": self.page_errors,
            "request_failures": self.request_failures,
            "third_party_failures": self.third_party_failures,
            "http_errors": self.http_errors,
            "decisions": self.decisions,
            "socket_events": self.socket_events,
        }

    def loud_problems(self) -> list[str]:
        """Non-benign problems that must never pass silently."""
        problems = [f"console error: {e['text'][:300]}" for e in self.console_errors()]
        problems += [f"page error: {e[:300]}" for e in self.page_errors]
        problems += [
            f"request failed: {f['method']} {f['url']} :: {f['failure']}"
            for f in self.real_request_failures()
        ]
        problems += [
            f"HTTP {h['status']}: {h['method']} {h['url']}" for h in self.http_errors
        ]
        return problems


# --- Shared helpers ------------------------------------------------------------


def new_artifacts_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ARTIFACTS_ROOT / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_evidence_json(path: Path, payload: dict[str, Any]) -> Path:
    """Write evidence as JSON, gzipping when the body exceeds 256 KiB.

    Tick streams are large even after in-page compaction. A `.json.gz` next
    to the screenshots is the artifact; small files stay plain `.json` so
    they are readable without a decompressor.
    """
    raw = json.dumps(payload, indent=2, default=str).encode()
    if len(raw) >= 256_000:
        gz_path = path.with_suffix(path.suffix + ".gz") if path.suffix == ".json" else path.with_suffix(".json.gz")
        gz_path.write_bytes(gzip.compress(raw, compresslevel=6))
        return gz_path
    path.write_bytes(raw)
    return path


def wait_for_start_enabled(page: Page) -> None:
    """#btn-start is disabled until the socket.io connection is up."""
    from playwright.sync_api import expect

    expect(page.locator("#btn-start")).to_be_enabled(timeout=SOCKET_CONNECT_MS)


def select_feedstock(page: Page, key: str = DEFAULT_FEEDSTOCK) -> str:
    """Select a feedstock like an operator. Returns the key actually used.

    Positive signal: the server-rendered feedstock card loads into
    #feedstock-info via htmx after the change event."""
    from playwright.sync_api import expect

    select = page.locator("#feedstock-select")
    expect(select).to_be_visible(timeout=PAGE_LOAD_MS)
    values = select.locator("option").evaluate_all(
        "(opts) => opts.map((o) => o.value).filter((v) => v)"
    )
    if not values:
        raise AssertionError("#feedstock-select has no real feedstock options rendered")
    chosen = key if key in values else values[0]
    select.select_option(chosen)
    # The change handler htmx-loads /partials/feedstock-card/<key> into #feedstock-info.
    expect(page.locator("#feedstock-info")).not_to_be_empty(timeout=FEEDSTOCK_CARD_MS)
    return chosen


def set_max_speed(page: Page) -> None:
    """'As fast as possible' radio (value=0)."""
    page.locator('input[name="speed"][value="0"]').check()


def click_start(page: Page) -> None:
    page.locator("#btn-start").click()


def wait_for_run_state(page: Page, last_hour: float, timeout_ms: int) -> tuple[str, str]:
    """Wait until the run advances past last_hour, or hits a terminal state.

    Returns (verdict, detail) where verdict is ADVANCED / COMPLETE / REFUSED /
    ERROR. Raises PlaywrightTimeoutError if nothing changes within timeout —
    that timeout IS the stall detector."""
    handle = page.wait_for_function(
        RUN_STATE_PREDICATE_JS, arg=last_hour, timeout=timeout_ms
    )
    value = str(handle.json_value())
    parts = value.split("::", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def wait_for_socket_event(
    page: Page,
    event: str,
    timeout_ms: int,
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    """Wait until an inbound socket event arrives in the tapped log.

    Returns the event's data dict. Raises PlaywrightTimeoutError on timeout —
    that timeout IS the 'server never answered' detector."""
    handle = page.wait_for_function(
        SOCKET_EVENT_PREDICATE_JS,
        arg={"event": event, "statuses": statuses or []},
        timeout=timeout_ms,
    )
    raw = handle.json_value()
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, dict) else {"_raw": data}


def find_run_id(evidence: EvidenceRecorder) -> str | None:
    for event in evidence.socket_events:
        data = event.get("data")
        if isinstance(data, dict) and data.get("run_id"):
            return str(data["run_id"])
    return None


def cancel_run_quietly(page: Page, evidence: EvidenceRecorder) -> None:
    """Best-effort cleanup: cancel a still-running sim so the suite is
    re-runnable and does not leak an active run on the shared dev server."""
    run_id = find_run_id(evidence)
    if not run_id:
        return
    try:
        page.request.post(f"{BASE_URL}/api/runs/{run_id}/cancel", timeout=30_000)
        evidence.note(f"cleanup: cancelled run {run_id}")
    except Exception as exc:
        evidence.note(f"cleanup: cancel of run {run_id} failed: {exc}")


__all__ = [
    "ARTIFACTS_ROOT",
    "BASE_URL",
    "DECISION_AUTO_ANSWER_JS",
    "DEFAULT_FEEDSTOCK",
    "EvidenceRecorder",
    "HEADED",
    "OPTIMIZER_BOUND_MS",
    "PAGE_LOAD_MS",
    "PlaywrightTimeoutError",
    "RUN_COMPLETE_MS",
    "SOCKET_TAP_JS",
    "START_ACK_MS",
    "STALL_THRESHOLD_MS",
    "STATUS_CHANGE_MS",
    "THERMAL_TRAIN_MS",
    "TICK_ADVANCE_MS",
    "WATCHDOG_WINDOW_MS",
    "cancel_run_quietly",
    "click_start",
    "new_artifacts_dir",
    "select_feedstock",
    "set_max_speed",
    "wait_for_run_state",
    "wait_for_socket_event",
    "wait_for_start_enabled",
    "write_evidence_json",
]
