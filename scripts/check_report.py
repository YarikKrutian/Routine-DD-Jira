#!/usr/bin/env python3
"""Validate a filled triage report (templates/report.html copy) before publishing.

Two groups of checks:
  data   — the JSON in <script id="report-data">: strict JSON, no placeholders, every
           breakdown sums to kpi.total_errors, KPI class counts match the pattern table,
           status matches cfg.status thresholds, no approximations, no obvious PII.
  layout — renders the page in headless Chromium at phone and desktop width and checks
           that the page actually rendered, has no JS errors and no horizontal overflow.

Output is JSON on stdout: {"ok": bool, "errors": [...], "warnings": [...], "screenshots": [...]}.
Exit code 1 when there are errors.

Examples:
  check_report.py /path/to/error-triage-2026-09-26-pm.html
  check_report.py report.html --no-layout
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CHROMIUM_CANDIDATES = ["/opt/pw-browsers/chromium", "chromium", "chromium-browser", "google-chrome"]
WIDTHS = (375, 1280)
MAX_NEW_TICKETS = 5
DATA_RE = re.compile(r'<script id="report-data" type="application/json">(.*?)</script>', re.S)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PHONE_RE = re.compile(r"(?<![\w:.\-/])\+?\d(?: ?\d){9,13}(?![\w:.\-/])")
URL_FIELDS = {"datadog_url"}


def walk_strings(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from walk_strings(v, key)
    elif isinstance(node, str):
        yield key, node


def check_data(html, errors, warnings):
    m = DATA_RE.search(html)
    if not m:
        errors.append('no <script id="report-data" type="application/json"> block')
        return None
    raw = m.group(1)
    if "{{" in html:
        errors.append("unreplaced {{...}} placeholder in the page")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        errors.append(f"report-data is not strict JSON: {e}")
        return None

    if d.get("example") is not False:
        errors.append('"example" must be false in a real report')

    k = d.get("kpi") or {}
    total = k.get("total_errors")
    patterns = d.get("patterns") or []

    def total_of(rows, name):
        if not rows:
            warnings.append(f"{name} is empty (rendered as 'no data')")
            return
        s = sum(r.get("count") or 0 for r in rows)
        if total is not None and s != total:
            errors.append(f"sum({name}) = {s} != kpi.total_errors = {total}")

    total_of(d.get("by_service"), "by_service")
    total_of(d.get("by_country"), "by_country")
    total_of(d.get("hourly"), "hourly")
    total_of(patterns, "patterns[].count")

    hourly = d.get("hourly") or []
    if hourly and len(hourly) != 12:
        errors.append(f"hourly has {len(hourly)} points, expected 12 (buckets from window start)")

    by_class = {"new": 0, "covered": 0, "noise": 0}
    for i, p in enumerate(patterns):
        c = p.get("class")
        if c not in by_class:
            errors.append(f"patterns[{i}].class = {c!r}, expected new|covered|noise")
            continue
        by_class[c] += 1
        if not isinstance(p.get("count"), int) or p["count"] <= 0:
            errors.append(f"patterns[{i}] ({p.get('pattern')!r}) has no real count in the window: {p.get('count')!r}")
        if c == "covered" and not p.get("jira"):
            errors.append(f"patterns[{i}] is 'covered' but has no jira key")
        if c == "new" and not p.get("confidence"):
            warnings.append(f"patterns[{i}] is 'new' without confidence")
        url = p.get("datadog_url")
        if url and not url.startswith("https://app.datadoghq"):
            errors.append(f"patterns[{i}].datadog_url is not a Datadog logs_explorer_url")
    for c, n in by_class.items():
        if k.get(c) != n:
            errors.append(f"kpi.{c} = {k.get(c)} but the table has {n} '{c}' rows")
    if k.get("patterns") != len(patterns):
        errors.append(f"kpi.patterns = {k.get('patterns')} but the table has {len(patterns)} rows")

    level = (d.get("status") or {}).get("level")
    n_new = by_class["new"]
    expected = "stable" if n_new == 0 else "watch" if n_new <= 2 else "degraded"
    if level != expected:
        errors.append(f"status.level = {level!r}, but {n_new} new -> {expected!r}")

    created = d.get("created_tickets") or []
    if len(created) > MAX_NEW_TICKETS:
        errors.append(f"{len(created)} created tickets > limit {MAX_NEW_TICKETS}")
    new_keys = {p.get("jira") for p in patterns if p.get("class") == "new"}
    for t in created:
        if t.get("key") not in new_keys:
            errors.append(f"created ticket {t.get('key')} has no matching 'new' row in patterns")

    for key, s in walk_strings(d):
        if key in URL_FIELDS:
            continue
        if "≈" in s or re.search(r"~\s?\d", s):
            errors.append(f"approximate number in {key!r}: {s[:80]!r}")
        if EMAIL_RE.search(s):
            errors.append(f"email-like value in {key!r}")
        if IPV4_RE.search(s):
            errors.append(f"IPv4-like value in {key!r}")
        if PHONE_RE.search(s):
            warnings.append(f"phone-like digits in {key!r}: check for PII")
    return d


PROBE_JS = """<script>window.__errs=[];addEventListener('error',e=>__errs.push(String(e.message||e)));</script>"""
HOST = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<iframe id="f" src="page.html" style="border:0;width:{w}px;height:{h}px"></iframe>
<script>
document.getElementById('f').addEventListener('load', () => setTimeout(() => {{
  const w = document.getElementById('f').contentWindow, doc = w.document, root = doc.documentElement;
  const app = doc.getElementById('app');
  const cw = root.clientWidth;
  const wide = [...doc.querySelectorAll('#app *')]
    .filter(e => !e.closest('.table-wrap') && e.getBoundingClientRect().right > cw + 1)
    .slice(0, 5).map(e => e.tagName.toLowerCase() + (e.className ? '.' + String(e.className).split(' ')[0] : ''));
  document.body.setAttribute('data-probe', JSON.stringify({{
    errs: w.__errs || [], children: app ? app.children.length : -1,
    text: app ? app.innerText.length : 0, sw: root.scrollWidth, cw, wide,
    rows: doc.querySelectorAll('#app tbody tr').length }}));
}}, 500));
</script></body></html>"""


def find_chromium():
    for c in CHROMIUM_CANDIDATES:
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p:
            return p
    return None


def check_layout(html, data, errors, warnings, shots_dir):
    chrome = find_chromium()
    if not chrome:
        warnings.append("layout check skipped: chromium not found")
        return []
    page = ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"{PROBE_JS}</head><body>{html}</body></html>")
    shots = []
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "page.html"), "w", encoding="utf-8") as f:
            f.write(page)
        base = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
                "--allow-file-access-from-files", "--virtual-time-budget=5000",
                "--no-first-run", "--disable-background-networking", "--disable-component-update"]
        for w in WIDTHS:
            host = os.path.join(tmp, f"host{w}.html")
            with open(host, "w", encoding="utf-8") as f:
                f.write(HOST.format(w=w, h=1200))
            try:
                res = subprocess.run(base + [f"--window-size={w + 40},1300", "--dump-dom", f"file://{host}"],
                                     capture_output=True, text=True, timeout=90)
            except subprocess.TimeoutExpired:
                errors.append(f"layout@{w}px: chromium timed out")
                continue
            m = re.search(r'data-probe="([^"]*)"', res.stdout)
            if not m:
                errors.append(f"layout@{w}px: page did not finish rendering (no probe result)")
                continue
            r = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&")
                           .replace("&lt;", "<").replace("&gt;", ">"))
            if r["errs"]:
                errors.append(f"layout@{w}px: JS errors: {r['errs'][:3]}")
            if r["children"] < 5 or r["text"] < 200:
                errors.append(f"layout@{w}px: report body is empty or truncated ({r['children']} sections)")
            if r["sw"] > r["cw"] + 1:
                errors.append(f"layout@{w}px: horizontal page scroll ({r['sw']}px > {r['cw']}px), "
                              f"overflowing: {r['wide']}")
            elif r["wide"]:
                warnings.append(f"layout@{w}px: elements wider than viewport: {r['wide']}")
            n = len((data or {}).get("patterns") or [])
            if data is not None and r["rows"] < n:
                errors.append(f"layout@{w}px: table shows {r['rows']} rows, data has {n} patterns")
            if shots_dir:
                os.makedirs(shots_dir, exist_ok=True)
                out = os.path.join(shots_dir, f"report-{w}.png")
                # Headless windows can't go below ~500px, so shoot the page inside a w-px iframe.
                shot_host = os.path.join(tmp, f"shot{w}.html")
                with open(shot_host, "w", encoding="utf-8") as f:
                    f.write(HOST.format(w=w, h=3000))
                subprocess.run(base + [f"--window-size={max(w, 500)},3000", f"--screenshot={out}",
                                       f"file://{shot_host}"],
                               capture_output=True, timeout=90)
                if os.path.exists(out):
                    shots.append(out)
    return shots


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("report", help="filled report HTML (copy of templates/report.html)")
    p.add_argument("--no-layout", action="store_true", help="skip the Chromium render check")
    p.add_argument("--screenshots", metavar="DIR", help="also save report-375.png / report-1280.png here")
    a = p.parse_args()

    with open(a.report, encoding="utf-8") as f:
        html = f.read()
    errors, warnings = [], []
    data = check_data(html, errors, warnings)
    shots = [] if a.no_layout else check_layout(html, data, errors, warnings, a.screenshots)
    print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings, "screenshots": shots},
                     ensure_ascii=False, indent=2))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
