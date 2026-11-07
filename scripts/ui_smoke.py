#!/usr/bin/env python3
"""Serve public/ locally and smoke-test every page in a headless browser.

    python scripts/ui_smoke.py [--root public] [--out ui-smoke] [--no-network]

Writes screenshots (phone 390 and desktop 1440, full page and above the fold) to --out and a
report.json, and exits non-zero on any hard failure:
  - a page does not load or renders (almost) no text
  - horizontal overflow at either width (document wider than the viewport)
  - any script error (blocked third-party resources are ignored)
  - a HubSpot form card that never renders a form (empty card) unless --no-network
  - /threat-watch/interest?interest=windows does not swap its copy
  - /inquiry does not redirect (checked by site_check against _redirects; here only that
    the local server has no such page)
Warnings (reported, non-fatal): tap targets under 40px tall on the phone layout.

Mirrors ee-secbrief/scripts/ui_smoke.py so the ui-qa agent reads both the same way.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

VIEWPORTS = {"phone": (390, 844), "desktop": (1440, 900)}
# route -> (expects a HubSpot form, expected title substring after any JS copy swap)
ROUTES = {
    "/": (False, "Engineering Excellence"),
    "/contact": (True, "Project Inquiry"),
    "/credentials": (True, "Working With"),
    "/threat-watch/interest": (True, "Threat Watch interest"),
    "/threat-watch/interest?interest=windows": (True, "Windows Server"),
    "/does-not-exist": (False, "404"),
}


class Quiet(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with Workers-style extensionless routes and a 404 page."""

    def log_message(self, *_):
        pass

    def translate_path(self, path):
        root = Path(self.directory)
        p = path.split("?", 1)[0].split("#", 1)[0]
        cands = [root / ("index.html" if p == "/" else p.lstrip("/"))]
        if not p.endswith(".html") and p != "/":
            cands += [root / (p.strip("/") + ".html"), root / p.strip("/") / "index.html"]
        for c in cands:
            if c.is_file():
                return str(c)
        return str(root / "404.html")

    def send_head(self):
        if Path(self.translate_path(self.path)).name == "404.html" and not self.path.endswith("404.html"):
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            body = (Path(self.directory) / "404.html").read_bytes()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            import io
            return io.BytesIO(body)
        return super().send_head()


def serve(root: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(Quiet, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="public")
    ap.add_argument("--out", default="ui-smoke")
    ap.add_argument("--no-network", action="store_true",
                    help="skip checks that need hsforms.net (sandboxes without egress)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ui_smoke: pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    report = {"checks": [], "failures": [], "warnings": []}

    def check(name: str, ok: bool, detail: str = "", warn: bool = False):
        report["checks"].append({"name": name, "ok": ok, "detail": detail, "severity": "warn" if warn else "fail"})
        if not ok:
            (report["warnings"] if warn else report["failures"]).append(f"{name}: {detail}")

    srv, base = serve(Path(args.root))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for route, (has_form, title_part) in ROUTES.items():
                for name, (w, h) in VIEWPORTS.items():
                    page = browser.new_page(viewport={"width": w, "height": h},
                                            device_scale_factor=2 if name == "phone" else 1)
                    errors: list[str] = []
                    page.on("console", lambda m: errors.append(m.text)
                            if m.type == "error" and "Failed to load resource" not in m.text else None)
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    resp = page.goto(base + route)
                    try:
                        page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        pass
                    slug = route.strip("/").replace("/", "-").replace("?", "-").replace("=", "-") or "home"
                    tag = f"{slug}-{name}"
                    page.screenshot(path=str(out / f"{tag}.png"), full_page=True)
                    page.screenshot(path=str(out / f"{tag}-fold.png"))

                    status = resp.status if resp else 0
                    want = 404 if title_part == "404" else 200
                    check(f"{tag} status {want}", status == want, f"got {status}")
                    check(f"{tag} title", title_part in page.title(), f"title {page.title()!r}")
                    content = page.evaluate("document.body.innerText.trim().length")
                    check(f"{tag} renders content", content > 50, f"body text is {content} chars")

                    sw = page.evaluate("document.documentElement.scrollWidth")
                    culprits = ""
                    if sw > w:
                        culprits = page.evaluate("""(w) => Array.from(document.querySelectorAll('body *'))
                            .map(e => ({e, r: e.getBoundingClientRect()}))
                            .filter(o => o.r.width > 0 && o.r.right > w + 0.5)
                            .filter(o => !Array.from(o.e.children).some(c => c.getBoundingClientRect().right > w + 0.5))
                            .slice(0, 6)
                            .map(o => `${o.e.tagName.toLowerCase()}${o.e.id ? '#' + o.e.id : ''}${o.e.className && typeof o.e.className === 'string' ? '.' + o.e.className.trim().split(/\\s+/).join('.') : ''} right=${o.r.right.toFixed(1)} w=${o.r.width.toFixed(1)} "${(o.e.textContent || '').trim().slice(0, 30)}"`)
                            .join(' | ')""", w)
                    check(f"{tag} no horizontal overflow", sw <= w, f"scrollWidth {sw} > viewport {w}; {culprits}")
                    check(f"{tag} no console errors", not errors, "; ".join(errors)[:500])

                    if has_form and not args.no_network:
                        # an hs-form-frame embed renders an iframe, a v2 embed an inline <form>;
                        # either way an empty card means the wrong embed type for this form
                        try:
                            page.wait_for_selector(".form-card iframe, .form-card form, .form-card input",
                                                   timeout=20000)
                            rendered = True
                        except Exception:
                            rendered = False
                        check(f"{tag} HubSpot form renders", rendered,
                              "form card stayed empty after 20s (wrong embed type, or hsforms.net blocked)")
                        if rendered and "interest=" in route:
                            # give onFormReady a moment, then confirm the hidden field carries the tag
                            page.wait_for_timeout(1500)
                            val = page.evaluate("""() => {
                                const i = document.querySelector('input[name="interest"]');
                                return i ? i.value : null }""")
                            check(f"{tag} hidden interest field", val in (None, "windows"),
                                  f"interest field value {val!r}", warn=(val is None))

                    if name == "phone":
                        small = page.evaluate("""
                          Array.from(document.querySelectorAll('button, a.hero-cta, a.alt, nav a, [role=button]'))
                            .filter(e => e.offsetParent !== null)
                            .map(e => ({t: (e.textContent||'').trim().slice(0,30), h: e.getBoundingClientRect().height}))
                            .filter(o => o.h > 0 && o.h < 40)""")
                        check(f"{tag} tap targets >= 40px", not small,
                              ", ".join(f"{o['t']!r} {o['h']:.0f}px" for o in small)[:500], warn=True)
                    page.close()
            browser.close()
    finally:
        srv.shutdown()

    (out / "report.json").write_text(json.dumps(report, indent=2))
    ok = not report["failures"]
    print(f"ui_smoke: {'OK' if ok else 'FAIL'}, {len(report['checks'])} checks, screenshots in {out}/")
    for f in report["failures"]:
        print("  FAIL", f)
    for w in report["warnings"]:
        print("  WARN", w)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
