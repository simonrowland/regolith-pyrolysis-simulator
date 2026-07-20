#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote

try:
    from playwright.sync_api import (
        Page,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ModuleNotFoundError as error:
    raise SystemExit(
        "Playwright is required. Install scripts/qa/requirements-report-viewer.txt "
        "and run `python -m playwright install chromium`."
    ) from error


RUN_IDS = [
    "fullyield-lunar-mare-1900c-mre25",
    "45454a0a69b44ca5a747e950a662ff8b",
    "013661d3a4cd4aeaacb4e59194d3a89a",
    "692727c23c544d37bb850fe3ac9b7dcb",
    "bea4fc8fcc3f4cbf859d2e8b7265e486",
    "live-star-demo",
]

REPORT_HEADINGS = [
    "Product-ledger metal projection — emitter whitelist order",
    "Process record — per-hour telemetry",
    "Account disposition",
    "Full terminal ledger",
    "Campaign results",
    "Metal taps & stage purity",
    "Wall risk, oxygen & pumping",
    "Terminal ceramic — cleaned melt",
    "Energy & two-price cost",
    "Provenance & confidence",
]

REPORT_DISCLOSURES = []


class Capture:
    def __init__(self, page: Page):
        self.events = []
        self.cursor = 0
        page.on(
            "console",
            lambda msg: self.events.append(
                {"kind": "console", "level": msg.type, "text": msg.text}
            )
            if msg.type == "error"
            else None,
        )
        page.on(
            "pageerror",
            lambda error: self.events.append(
                {"kind": "pageerror", "text": str(error)}
            ),
        )
        page.on(
            "requestfailed",
            lambda request: self.events.append(
                {
                    "kind": "requestfailed",
                    "url": request.url,
                    "text": request.failure or "request failed",
                }
            ),
        )
        page.on(
            "response",
            lambda response: self.events.append(
                {
                    "kind": "http",
                    "status": response.status,
                    "url": response.url,
                }
            )
            if response.status >= 400
            else None,
        )

    def drain(self) -> list[dict]:
        events = self.events[self.cursor :]
        self.cursor = len(self.events)
        return events


class Audit:
    def __init__(self, out_dir: Path, round_number: int):
        self.out_dir = out_dir
        self.round_number = round_number
        self.issues = []
        self.screens = []
        self.metrics = {}

    def issue(self, screen: str, kind: str, detail: str) -> None:
        self.issues.append({"screen": screen, "kind": kind, "detail": detail})

    def screenshot(self, page: Page, screen: str) -> None:
        target = self.out_dir / f"round-{self.round_number:02d}-{slug(screen)}.png"
        page.screenshot(path=str(target), full_page=True)
        self.screens.append(str(target))

    def check_events(self, capture: Capture, screen: str) -> None:
        for event in capture.drain():
            self.issue(screen, event["kind"], json.dumps(event, sort_keys=True))

    def check_dom(self, page: Page, screen: str, expect_sections: bool = True) -> None:
        body_text = page.locator("body").inner_text()
        for pattern, label in [
            (r"\[object Object\]", "raw object"),
            (r"\bNaN\b", "NaN"),
            (r"\bundefined\b", "undefined"),
            (r"\b[0-9a-f]{32}\b", "raw run hash"),
        ]:
            match = re.search(pattern, body_text, re.IGNORECASE)
            if match:
                self.issue(screen, "dom", f"Visible {label}: {match.group(0)}")
        fatals = page.locator(".fatal:visible")
        if fatals.count():
            self.issue(screen, "dom", f"Fatal panel visible: {fatals.first.inner_text()[:300]}")
        sections = page.locator("section:visible")
        if expect_sections and sections.count() == 0:
            self.issue(screen, "dom", "No report sections rendered")
        for index in range(sections.count()):
            text = sections.nth(index).inner_text().strip()
            if len(text) < 12:
                self.issue(screen, "dom", f"Section {index + 1} rendered blank")
        unnamed_controls = page.locator(
            "button:visible, input:visible, select:visible, textarea:visible, a[href]:visible"
        ).evaluate_all(
            """els => els.filter(el => {
              const labelled = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
              const native = el.labels && Array.from(el.labels).some(label => label.textContent.trim());
              return !labelled && !native && !(el.textContent || el.value || '').trim();
            }).map(el => `${el.tagName.toLowerCase()}#${el.id || '(no-id)'}`)"""
        )
        if unnamed_controls:
            self.issue(screen, "accessibility", f"Unnamed controls: {unnamed_controls}")

    def check_mobile_overflow(self, page: Page, screen: str) -> None:
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        if overflow > 1:
            self.issue(screen, "layout", f"Document overflows mobile viewport by {overflow}px")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]


def inspect_page(page: Page, url: str, out_dir: Path, name: str) -> dict:
    capture = Capture(page)
    response = page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(300)
    screenshot = out_dir / f"inspect-{slug(name)}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    controls = page.locator("button, input, select, textarea, a[href]").evaluate_all(
        """els => els.map((el, index) => ({
          index,
          tag: el.tagName.toLowerCase(),
          id: el.id || null,
          name: el.getAttribute('name'),
          type: el.getAttribute('type'),
          href: el.getAttribute('href'),
          aria: el.getAttribute('aria-label'),
          title: el.getAttribute('title'),
          text: (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 160),
          disabled: Boolean(el.disabled),
        }))"""
    )
    return {
        "name": name,
        "url": page.url,
        "status": response.status if response else None,
        "title": page.title(),
        "body_text": page.locator("body").inner_text()[:6000],
        "controls": controls,
        "events": capture.events,
        "screenshot": str(screenshot),
    }


def inspect(base_url: str, out_dir: Path) -> int:
    pages = [
        ("library", f"{base_url}/report/library.html"),
        ("settings", f"{base_url}/report/settings.html"),
    ] + [
        (
            f"run-{run_id}",
            f"{base_url}/report/?run={quote(run_id, safe='')}",
        )
        for run_id in RUN_IDS
    ]
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        for name, url in pages:
            page = context.new_page()
            results.append(inspect_page(page, url, out_dir, name))
            page.close()
        browser.close()
    result_path = out_dir / "inspection.json"
    result_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    event_count = sum(len(result["events"]) for result in results)
    navigation_failures = sum(
        result["status"] is None or result["status"] >= 400 for result in results
    )
    print(
        f"INSPECTED {len(results)} pages; captured {event_count} error events and "
        f"{navigation_failures} navigation failures; {result_path}"
    )
    return 1 if event_count or navigation_failures else 0


def wait_for_render(page: Page, root_selector: str) -> None:
    page.wait_for_function(
        "selector => (document.querySelector(selector)?.innerText || '').trim().length > 0",
        arg=root_selector,
    )
    page.wait_for_timeout(100)


def visit_report(
    page: Page,
    audit: Audit,
    base_url: str,
    run_id: str,
    expected_steps: int,
    expected_status: str,
    *,
    sweep_steps: bool,
    expect_zero_source_o2: bool = False,
    screen_prefix: str = "desktop",
    standalone: bool = False,
    capture: Capture | None = None,
) -> None:
    screen = f"{screen_prefix}-report-{run_id}"
    capture = capture or Capture(page)
    report_path = "/report/index.html" if standalone else "/report/"
    page.goto(
        f"{base_url}{report_path}?run={quote(run_id, safe='')}",
        wait_until="networkidle",
    )
    wait_for_render(page, "#report")
    audit.check_dom(page, screen, expect_sections=True)
    body_text = page.locator("#report").inner_text()
    if f"EXECUTION STATUS: {expected_status}" not in body_text:
        audit.issue(screen, "semantics", f"Expected execution status {expected_status}")
    if expect_zero_source_o2:
        value_lines = re.findall(
            r"SOURCE-SIDE O₂ POTENTIAL[^\n]*\n([^\n]+)",
            body_text,
            re.IGNORECASE,
        )
        source_o2_values = []
        for value_line in value_lines:
            match = re.fullmatch(
                r"\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?)\s*kg\s*",
                value_line,
                re.IGNORECASE,
            )
            if match:
                source_o2_values.append(float(match.group(1)))
        if not source_o2_values or any(value != 0.0 for value in source_o2_values):
            audit.issue(
                screen,
                "semantics",
                f"Zero-yield fixture source-side O₂ values were {source_o2_values or value_lines}",
            )
    section_count = page.locator("section:visible").count()
    if section_count != 10:
        audit.issue(screen, "dom", f"Rendered {section_count} report sections; expected 10")
    headings = [
        re.sub(r"^\d{2}", "", heading.strip())
        for heading in page.locator("section:visible h2").all_inner_texts()
    ]
    if headings != REPORT_HEADINGS:
        audit.issue(screen, "dom", f"Report section identities differ: {headings}")
    details = page.locator("details:visible")
    disclosure_names = []
    for index in range(details.count()):
        disclosure = details.nth(index)
        try:
            summary = disclosure.locator("summary").inner_text().strip()
            if not summary:
                audit.issue(screen, "interaction", f"Disclosure {index + 1} has no identity")
            disclosure_names.append(summary)
            disclosure.locator("summary").click()
            if not disclosure.evaluate("el => el.open"):
                audit.issue(screen, "interaction", f"Disclosure {index + 1} did not open")
            expanded = disclosure.inner_text().strip()
            detail_text = expanded.removeprefix(summary).strip()
            if len(detail_text) < 12:
                audit.issue(
                    screen,
                    "interaction",
                    f"Disclosure {index + 1} ({summary}) opened without detail content",
                )
            disclosure.locator("summary").click()
            if disclosure.evaluate("el => el.open"):
                audit.issue(screen, "interaction", f"Disclosure {index + 1} did not close")
        except Exception as error:
            audit.issue(screen, "interaction", f"Disclosure {index + 1}: {error}")
    if len(disclosure_names) != len(set(disclosure_names)):
        audit.issue(screen, "interaction", f"Duplicate disclosure identities: {disclosure_names}")
    if disclosure_names != REPORT_DISCLOSURES:
        audit.issue(
            screen,
            "interaction",
            f"Disclosure identities differ: expected {REPORT_DISCLOSURES}, got {disclosure_names}",
        )
    stepper = page.locator("#stepper")
    if expected_steps == 0:
        if stepper.count():
            audit.issue(screen, "dom", "Zero-timestep failed run rendered a stepper")
    elif stepper.count() != 1:
        audit.issue(screen, "dom", f"Expected one stepper for {expected_steps} timesteps")
    else:
        maximum = int(stepper.get_attribute("max") or -1)
        if maximum != expected_steps - 1:
            audit.issue(
                screen,
                "stepper",
                f"Stepper max {maximum}; expected {expected_steps - 1}",
            )
        if sweep_steps:
            try:
                failures = stepper.evaluate(
                    r"""async (el) => {
                  const failures = [];
                  const max = Number(el.max);
                  let previousOutput = null;
                  for (let index = 0; index <= max; index += 1) {
                    el.value = String(index);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    await new Promise(resolve => setTimeout(resolve, 0));
                    const output = document.querySelector('#step-output');
                    const outputText = output?.textContent.trim() || '';
                    const reportText = document.querySelector('#report')?.innerText || '';
                    const blankSection = Array.from(document.querySelectorAll('section')).findIndex(
                      section => section.innerText.trim().length < 12
                    );
                    if (el.getAttribute('aria-valuenow') !== String(index)) failures.push(`aria:${index}`);
                    if (!outputText) failures.push(`output:${index}`);
                    if (index > 0 && outputText === previousOutput) failures.push(`stale:${index}`);
                    if (/\[object Object\]|\bNaN\b|\bundefined\b|\b[0-9a-f]{32}\b/i.test(reportText)) {
                      failures.push(`dom:${index}`);
                    }
                    if (blankSection >= 0) failures.push(`blank-section-${blankSection + 1}:${index}`);
                    if (document.querySelectorAll('section').length !== 10) failures.push(`sections:${index}`);
                    previousOutput = outputText;
                  }
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  return failures.slice(0, 20);
                }"""
                )
                page.wait_for_timeout(50)
                if failures:
                    audit.issue(screen, "stepper", f"Full-range failures: {failures}")
                audit.check_dom(page, f"{screen}-final-timestep", expect_sections=True)
            except Exception as error:
                audit.issue(screen, "stepper", f"Full-range sweep failed: {error}")
            try:
                stepper.evaluate(
                    """el => {
                  el.value = '0';
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                }"""
                )
                if not page.locator("#step-prev").is_disabled():
                    audit.issue(screen, "stepper", "Previous button enabled at first timestep")
                if expected_steps > 1:
                    page.locator("#step-next").click()
                    if stepper.input_value() != "1":
                        audit.issue(screen, "stepper", "Next button did not advance")
                    stepper.evaluate(
                        """el => {
                      el.value = el.max;
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                    }"""
                    )
                    if not page.locator("#step-next").is_disabled():
                        audit.issue(screen, "stepper", "Next button enabled at final timestep")
                    page.locator("#step-prev").click()
                    if stepper.input_value() != str(expected_steps - 2):
                        audit.issue(screen, "stepper", "Previous button did not retreat")
            except Exception as error:
                audit.issue(screen, "stepper", f"Boundary controls failed: {error}")
    audit.metrics[screen] = {
        "sections": section_count,
        "timesteps_exercised": expected_steps if sweep_steps else 0,
    }
    audit.screenshot(page, screen)
    audit.check_events(capture, screen)


def audit_library(
    page: Page, audit: Audit, base_url: str, capture: Capture | None = None
) -> None:
    screen = "desktop-library"
    capture = capture or Capture(page)
    mutations = []
    page.on(
        "request",
        lambda request: mutations.append(f"{request.method} {request.url}")
        if request.method not in {"GET", "HEAD", "OPTIONS"}
        else None,
    )
    page.goto(f"{base_url}/report/library.html", wait_until="networkidle")
    wait_for_render(page, "#library")
    audit.check_dom(page, screen)
    initial_cards = page.locator(".run-card:visible").count()
    if initial_cards < 6:
        audit.issue(screen, "library", f"Only {initial_cards} run cards rendered")
    expected_folders = {}
    try:
        expected_folders = page.evaluate(
            """async () => {
              const [staticResponse, liveResponse] = await Promise.all([
                fetch('./runs-index.json'),
                fetch('/api/runs'),
              ]);
              const staticRuns = await staticResponse.json();
              const liveRuns = (await liveResponse.json()).map(run => ({ ...run, live: true }));
              const liveIds = new Set(liveRuns.map(run => String(run.run_id)));
              const runs = [...liveRuns, ...staticRuns.filter(run => !liveIds.has(String(run.run_id)))];
              const ids = entries => entries.map(run => String(run.run_id)).sort();
              return {
                'All': ids(runs),
                'Favorites': ids(runs.filter(run => Boolean(run.starred))),
                'My runs': ids(runs.filter(run => run.folder === 'My runs')),
                'Default runs': ids(runs.filter(run => run.folder === 'Default runs')),
                'Bootstrap ladder': ids(runs.filter(run => run.folder === 'Bootstrap ladder')),
              };
            }"""
        )
    except Exception as error:
        audit.issue(screen, "library", f"Could not derive folder membership from indexes: {error}")
    for folder in ["Favorites", "My runs", "Default runs", "Bootstrap ladder", "All"]:
        try:
            button = page.locator("[data-folder]").filter(
                has_text=re.compile(rf"^{re.escape(folder)}\b")
            )
            if button.count() != 1:
                audit.issue(screen, "library", f"Folder control missing: {folder}")
                continue
            expected_count_match = re.search(r"(\d+)\s*$", button.inner_text())
            button.click()
            if button.get_attribute("aria-pressed") != "true":
                audit.issue(screen, "library", f"Folder did not activate: {folder}")
            visible_count = page.locator(".run-card:visible").count()
            if expected_count_match and visible_count != int(expected_count_match.group(1)):
                audit.issue(
                    screen,
                    "library",
                    f"{folder} shows {visible_count} cards; control promised {expected_count_match.group(1)}",
                )
            actual_ids = page.locator(
                ".run-card:visible [data-star]"
            ).evaluate_all("els => els.map(el => el.dataset.star).sort()")
            expected_ids = expected_folders.get(folder)
            if expected_ids is not None and actual_ids != expected_ids:
                audit.issue(
                    screen,
                    "library",
                    f"{folder} membership differs: expected {expected_ids}, got {actual_ids}",
                )
        except Exception as error:
            audit.issue(screen, "library", f"Folder {folder} failed: {error}")
    filter_input = page.locator("#run-filter")
    try:
        filter_input.fill("")
        page.locator("#run-sort").select_option("name")
        names = page.locator(".run-card:visible .run-card-title h2").all_inner_texts()
        if names != sorted(names, key=str.casefold):
            audit.issue(screen, "library", "Name sort did not order rendered card titles")
        page.locator("#run-sort").select_option("status")
        status_text = page.locator(".run-card:visible .run-card-title .ct").all_inner_texts()
        status_keys = []
        for value in status_text:
            match = re.search(
                r"\b(demo metadata|failed|ok|partial|running|queued|unknown)\b",
                value.lower(),
            )
            status_keys.append(match.group(1) if match else value.casefold())
        if status_keys != sorted(status_keys):
            audit.issue(screen, "library", "Status sort did not order rendered status labels")
        page.locator("#run-sort").select_option("created")
        summaries = page.locator(".run-card:visible .run-summary").all_inner_texts()
        dates = [
            match.group(0)
            for summary in summaries
            if (match := re.search(r"\b\d{4}-\d{2}-\d{2}\b", summary))
        ]
        if dates != sorted(dates, reverse=True):
            audit.issue(screen, "library", "Created sort did not order rendered creation dates newest-first")
    except Exception as error:
        audit.issue(screen, "library", f"Sort coverage failed: {error}")
    try:
        filter_input.fill("45454a0a")
        filtered_cards = page.locator(".run-card:visible").count()
        if filtered_cards != 1:
            audit.issue(screen, "library", f"Filter returned {filtered_cards} cards; expected 1")
        filter_input.fill("no-such-regolith-run")
        page.wait_for_function(
            "document.querySelector('#run-list')?.innerText.toLowerCase().includes('no matching runs')"
        )
    except Exception as error:
        audit.issue(screen, "library", f"Filter and empty state failed: {error}")
    try:
        filter_input.fill("")
        static_star_id = page.evaluate(
            """() => {
              const card = Array.from(document.querySelectorAll('.run-card')).find(item => {
                const target = item.querySelector('[data-load]')?.dataset.load || '';
                return target.endsWith('.html');
              });
              return card?.querySelector('[data-star]')?.dataset.star || null;
            }"""
        )
        if not static_star_id:
            audit.issue(screen, "library", "Client-side sample star control missing")
        else:
            selector = f'[data-star="{static_star_id}"]'
            star = page.locator(selector)
            original = star.get_attribute("aria-pressed")
            expected = "false" if original == "true" else "true"
            try:
                star.click()
                page.wait_for_function(
                    "payload => document.querySelector(payload.selector)?.getAttribute('aria-pressed') === payload.expected",
                    arg={"selector": selector, "expected": expected},
                )
            finally:
                current = page.locator(selector).get_attribute("aria-pressed")
                if current != original:
                    page.locator(selector).click()
                    page.wait_for_function(
                        "payload => document.querySelector(payload.selector)?.getAttribute('aria-pressed') === payload.expected",
                        arg={"selector": selector, "expected": original},
                    )
        if mutations:
            audit.issue(screen, "library", f"Sample star caused shared-state mutation: {mutations}")
    except Exception as error:
        audit.issue(screen, "library", f"Star round trip failed: {error}")
    try:
        filter_input.fill("45454a0a")
        load = page.locator('.run-card:has-text("45454a0a") .load-button')
        if load.count() != 1:
            audit.issue(screen, "library", "45454a0a Load control missing")
        else:
            load.click()
            page.wait_for_url(re.compile(r"/report/(?:index\.html)?\?run=45454a0a"))
            wait_for_render(page, "#report")
            if "EXECUTION STATUS: OK" not in page.locator("#report").inner_text():
                audit.issue(screen, "library", "Load did not open the selected run")
    except Exception as error:
        audit.issue(screen, "library", f"Load action failed: {error}")
    finally:
        if "/report/library.html" not in page.url:
            page.goto(f"{base_url}/report/library.html", wait_until="networkidle")
            wait_for_render(page, "#library")
    audit.metrics[screen] = {"initial_cards": initial_cards, "folders_exercised": 5}
    audit.screenshot(page, screen)
    audit.check_events(capture, screen)


def audit_settings(
    page: Page, audit: Audit, base_url: str, capture: Capture | None = None
) -> None:
    run_id = "45454a0a69b44ca5a747e950a662ff8b"
    screen = "desktop-settings"
    capture = capture or Capture(page)
    page.goto(
        f"{base_url}/report/settings.html?run={run_id}",
        wait_until="networkidle",
    )
    wait_for_render(page, "#settings")
    audit.check_dom(page, screen)
    download_path = audit.out_dir / f"round-{audit.round_number:02d}-captured-header.yaml"
    download_bytes = 0
    try:
        with page.expect_download() as download_info:
            page.locator("#download-run").click()
        download = download_info.value
        download.save_as(str(download_path))
        download_bytes = download_path.stat().st_size
        if download_bytes == 0:
            audit.issue(screen, "settings", "Captured-header download was empty")
        if "schema_version" not in download_path.read_text(encoding="utf-8"):
            audit.issue(screen, "settings", "Captured-header download lacks schema_version")
    except Exception as error:
        audit.issue(screen, "settings", f"Captured-header download failed: {error}")
    try:
        page.get_by_role("link", name="← Back to report").click()
        page.wait_for_url(re.compile(r"/report/(?:index\.html)?\?run=45454a0a"))
        wait_for_render(page, "#report")
        if "EXECUTION STATUS: OK" not in page.locator("#report").inner_text():
            audit.issue(screen, "settings", "Back-to-report link lost the selected run")
    except Exception as error:
        audit.issue(screen, "settings", f"Back-to-report link failed: {error}")
    finally:
        page.goto(
            f"{base_url}/report/settings.html?run={run_id}",
            wait_until="networkidle",
        )
        wait_for_render(page, "#settings")
    audit.metrics[screen] = {"download_bytes": download_bytes}
    audit.screenshot(page, screen)
    audit.check_events(capture, screen)


def audit_hash_deep_link(
    page: Page, audit: Audit, base_url: str, capture: Capture | None = None
) -> None:
    run_id = "45454a0a69b44ca5a747e950a662ff8b"
    screen = "deep-link-stepper"
    capture = capture or Capture(page)
    page.goto(
        f"{base_url}/report/?run={run_id}#stepper",
        wait_until="networkidle",
    )
    wait_for_render(page, "#report")
    target = page.locator("#stepper")
    if target.count() != 1:
        audit.issue(screen, "deep-link", "Hash target #stepper did not render")
    else:
        in_view = target.evaluate(
            "el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; }"
        )
        if not in_view:
            audit.issue(screen, "deep-link", "Dynamic #stepper hash target was not scrolled into view")
    audit.screenshot(page, screen)
    audit.check_events(capture, screen)


def audit_variant_context(
    context,
    audit: Audit,
    base_url: str,
    prefix: str,
    *,
    mobile: bool,
) -> None:
    targets = [
        ("report", f"{base_url}/report/?run=45454a0a69b44ca5a747e950a662ff8b", "#report"),
        ("library", f"{base_url}/report/library.html", "#library"),
        (
            "settings",
            f"{base_url}/report/settings.html?run=45454a0a69b44ca5a747e950a662ff8b",
            "#settings",
        ),
    ]
    for name, url, root in targets:
        screen = f"{prefix}-{name}"
        page = context.new_page()
        capture = Capture(page)
        try:
            page.goto(url, wait_until="networkidle")
            wait_for_render(page, root)
            audit.check_dom(page, screen)
            if mobile:
                audit.check_mobile_overflow(page, screen)
            else:
                dark = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
                if not dark:
                    audit.issue(screen, "theme", "Dark color-scheme preference was not active")
                background = page.evaluate("getComputedStyle(document.body).backgroundColor")
                channels = [int(value) for value in re.findall(r"\d+", background)[:3]]
                if len(channels) == 3 and sum(channels) / 3 > 170:
                    audit.issue(screen, "theme", f"Dark mode retained a light body background: {background}")
            audit.screenshot(page, screen)
        except Exception as error:
            audit.issue(screen, "driver", str(error))
        audit.check_events(capture, screen)
        page.close()


def full(base_url: str, out_dir: Path, round_number: int) -> int:
    run_specs = {
        "fullyield-lunar-mare-1900c-mre25": {"steps": 898, "status": "FAILED"},
        "45454a0a69b44ca5a747e950a662ff8b": {"steps": 61, "status": "OK"},
        "013661d3a4cd4aeaacb4e59194d3a89a": {
            "steps": 1,
            "status": "PARTIAL",
            "zero_source_o2": True,
        },
        "692727c23c544d37bb850fe3ac9b7dcb": {"steps": 0, "status": "FAILED"},
        "bea4fc8fcc3f4cbf859d2e8b7265e486": {"steps": 22, "status": "FAILED"},
        "live-star-demo": {"steps": 2, "status": "OK"},
    }
    audit = Audit(out_dir, round_number)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
        for run_id, spec in run_specs.items():
            page = desktop.new_page()
            capture = Capture(page)
            try:
                visit_report(
                    page,
                    audit,
                    base_url,
                    run_id,
                    spec["steps"],
                    spec["status"],
                    sweep_steps=True,
                    expect_zero_source_o2=spec.get("zero_source_o2", False),
                    capture=capture,
                )
            except Exception as error:
                audit.issue(f"desktop-report-{run_id}", "driver", str(error))
            finally:
                audit.check_events(capture, f"desktop-report-{run_id}")
            page.close()
        page = desktop.new_page()
        capture = Capture(page)
        try:
            audit_library(page, audit, base_url, capture)
        except Exception as error:
            audit.issue("desktop-library", "driver", str(error))
        finally:
            audit.check_events(capture, "desktop-library")
        page.close()
        page = desktop.new_page()
        capture = Capture(page)
        try:
            audit_settings(page, audit, base_url, capture)
        except Exception as error:
            audit.issue("desktop-settings", "driver", str(error))
        finally:
            audit.check_events(capture, "desktop-settings")
        page.close()
        page = desktop.new_page()
        capture = Capture(page)
        try:
            audit_hash_deep_link(page, audit, base_url, capture)
        except Exception as error:
            audit.issue("deep-link-stepper", "driver", str(error))
        finally:
            audit.check_events(capture, "deep-link-stepper")
        page.close()
        page = desktop.new_page()
        capture = Capture(page)
        try:
            visit_report(
                page,
                audit,
                base_url,
                "45454a0a69b44ca5a747e950a662ff8b",
                61,
                "OK",
                sweep_steps=False,
                screen_prefix="standalone",
                standalone=True,
                capture=capture,
            )
        except Exception as error:
            audit.issue("standalone-report", "driver", str(error))
        finally:
            audit.check_events(capture, "standalone-report")
        page.close()
        desktop.close()
        dark = browser.new_context(
            viewport={"width": 1440, "height": 1000}, color_scheme="dark"
        )
        audit_variant_context(dark, audit, base_url, "dark", mobile=False)
        dark.close()
        mobile = browser.new_context(viewport={"width": 375, "height": 812})
        audit_variant_context(mobile, audit, base_url, "mobile", mobile=True)
        mobile.close()
        browser.close()
    result = {
        "round": round_number,
        "base_url": base_url,
        "runs": run_specs,
        "metrics": audit.metrics,
        "issues": audit.issues,
        "screenshots": audit.screens,
    }
    result_path = out_dir / f"round-{round_number:02d}.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"ROUND {round_number}: {len(audit.issues)} real-error candidates; "
        f"{len(audit.screens)} screenshots; {result_path}"
    )
    return 1 if audit.issues else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8490")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("instance/qa-report-viewer"),
    )
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("command", choices=["inspect", "full"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "inspect":
        return inspect(args.base_url.rstrip("/"), args.out)
    return full(args.base_url.rstrip("/"), args.out, args.round)


if __name__ == "__main__":
    raise SystemExit(main())
