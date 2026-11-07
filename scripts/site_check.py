#!/usr/bin/env python3
"""Static consistency checks for the EE site (public/). No dependencies.

    python scripts/site_check.py [--root public] [--json report.json]

Hard failures (exit 1):
  - em dashes anywhere in a page (titles, copy, scripts)
  - nav links differ between pages, or a page's nav lacks the Threat Watch link
  - a page lacks <title>, meta description, og:title, og:description, og:image or og:url
  - a <title> or og:title that does not end in " · EE" or "Engineering Excellence"
  - sitemap.xml does not list exactly the public pages (404 excluded)
  - an internal href/src that resolves to no file, or a #anchor with no matching id
  - an http:// link
  - a HubSpot form embedded with the wrong embed type (legacy-editor forms need v2 and
    render an empty card under hs-form-frame)
  - a _redirects source that still exists as a file, or a target that resolves to nothing
Warnings (reported, exit 0):
  - footer year is not the current year
  - a page nothing else links to (orphan)
  - external links without rel="noopener" when target="_blank"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

SITE = "https://engexcellence.co"
THREAT_WATCH = "https://threats.engexcellence.co"

# HubSpot forms used on the site and which embed each one needs.
#   frame: new-editor form, works with <div class="hs-form-frame" ...> + embed/<portal>.js
#   v2:    legacy-editor form, works only with embed/v2.js + hbspt.forms.create({...})
HUBSPOT_FORMS = {
    "e86624b2-fa8b-4ff6-980a-a22c2b22353b": "frame",  # EE project inquiry (/contact)
    "39287be1-b479-4b36-85a9-461b34eb011f": "v2",     # Threat Watch Interest (/threat-watch/interest)
}

TITLE_OK = re.compile(r"( · EE|Engineering Excellence)$")


class Page(HTMLParser):
    """Collects the handful of things the checks need from one HTML file."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.metas: dict[str, str] = {}
        self.ids: set[str] = set()
        self.links: list[tuple[str, str, dict[str, str]]] = []  # (tag, url, attrs)
        self.nav: list[str] = []
        self.hs_frames: list[str] = []
        self.scripts: list[str] = []
        self._in_title = False
        self._in_nav = 0
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "nav":
            self._in_nav += 1
        if tag == "script":
            self._in_script = True
        if tag == "meta":
            key = a.get("name") or a.get("property")
            if key and "content" in a:
                self.metas[key] = a["content"]
        if "id" in a:
            self.ids.add(a["id"])
        if a.get("class") and "hs-form-frame" in a["class"].split():
            self.hs_frames.append(a.get("data-form-id", ""))
        for attr in ("href", "src"):
            if attr in a and a[attr] and not a[attr].startswith(("mailto:", "tel:", "data:", "javascript:")):
                self.links.append((tag, a[attr], a))
                if attr == "href" and self._in_nav and tag == "a":
                    self.nav.append(a[attr])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "nav":
            self._in_nav -= 1
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_script:
            self.scripts.append(data)


def route_of(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel[: -len(".html")]


def norm_nav(href: str) -> str:
    # "#book" on the homepage and "/#book" elsewhere are the same link
    return "/" + href if href.startswith("#") else href


def resolve(root: Path, url: str) -> Path | None:
    """Map a root-relative URL to a file under root, mimicking Workers static assets."""
    p = urlsplit(url).path
    if not p.startswith("/"):
        return None
    p = p.rstrip("/") or "/index.html"
    cands = [root / p.lstrip("/")]
    if not p.endswith(".html"):
        cands += [root / (p.lstrip("/") + ".html"), root / p.lstrip("/") / "index.html"]
    for c in cands:
        if c.is_file():
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="public")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    root = Path(args.root)

    failures: list[str] = []
    warnings: list[str] = []
    fail, warn = failures.append, warnings.append

    html_files = sorted(root.rglob("*.html"))
    pages: dict[Path, Page] = {}
    for f in html_files:
        text = f.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if "—" in line:
                fail(f"{f}:{n}: em dash: {line.strip()[:80]}")
            if re.search(r"""(href|src)=["']http://""", line):
                fail(f"{f}:{n}: http:// link: {line.strip()[:80]}")
        p = Page()
        p.feed(text)
        pages[f] = p

    public_pages = {f: p for f, p in pages.items() if f.name != "404.html"}
    routes = {route_of(f, root) for f in public_pages}

    # head metadata and titles
    for f, p in pages.items():
        if not p.title.strip():
            fail(f"{f}: no <title>")
        elif not TITLE_OK.search(p.title.strip()):
            fail(f"{f}: <title> {p.title.strip()!r} should end in ' · EE' or 'Engineering Excellence'")
        if f.name == "404.html":
            continue
        for key in ("description", "og:title", "og:description", "og:image", "og:url"):
            if key not in p.metas:
                fail(f"{f}: missing <meta {key}>")
        og = p.metas.get("og:title", "")
        if og and not TITLE_OK.search(og):
            fail(f"{f}: og:title {og!r} should end in ' · EE' or 'Engineering Excellence'")
        og_url = p.metas.get("og:url", "")
        if og_url and og_url != SITE + route_of(f, root):
            fail(f"{f}: og:url {og_url} != {SITE + route_of(f, root)}")

    # nav must be identical across public pages and include Threat Watch
    navs = {f: [norm_nav(h) for h in p.nav] for f, p in public_pages.items()}
    if navs:
        baseline_f = root / "index.html" if (root / "index.html") in navs else next(iter(navs))
        baseline = navs[baseline_f]
        for f, nav in navs.items():
            if not nav:
                fail(f"{f}: no <nav> links")
            elif nav != baseline:
                fail(f"{f}: nav differs from {baseline_f.name}: {nav} vs {baseline}")
            if THREAT_WATCH not in nav:
                fail(f"{f}: nav has no link to {THREAT_WATCH}")

    # sitemap lists exactly the public pages
    sm = root / "sitemap.xml"
    if not sm.is_file():
        fail("public/sitemap.xml missing")
    else:
        locs = set(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm.read_text()))
        sm_routes = set()
        for loc in locs:
            if not loc.startswith(SITE):
                fail(f"sitemap: {loc} is not under {SITE}")
                continue
            sm_routes.add(loc[len(SITE):] or "/")
        for r in sorted(routes - sm_routes):
            fail(f"sitemap: page {r} not listed")
        for r in sorted(sm_routes - routes):
            fail(f"sitemap: {r} listed but no page exists")

    # internal links, anchors, external targets
    inbound: dict[Path, set[Path]] = {f: set() for f in public_pages}
    for f, p in pages.items():
        for tag, url, a in p.links:
            if url.startswith(("https://", "//")):
                if url.startswith(SITE + "/") or url == SITE:
                    url = url[len(SITE):] or "/"
                else:
                    if tag == "a" and a.get("target") == "_blank" and "noopener" not in a.get("rel", ""):
                        warn(f"{f}: target=_blank without rel=noopener: {url}")
                    continue
            if url.startswith("#"):
                target_file, frag = f, url[1:]
            else:
                target_file = resolve(root, url)
                frag = urlsplit(url).fragment
                if target_file is None:
                    fail(f"{f}: <{tag}> {url} resolves to no file under {root}")
                    continue
                if target_file in inbound and target_file != f:
                    inbound[target_file].add(f)
            if frag and target_file.suffix == ".html" and frag not in pages[target_file].ids:
                fail(f"{f}: anchor #{frag} not found in {target_file.relative_to(root)}")
    for f, srcs in inbound.items():
        if not srcs and f != root / "index.html":
            warn(f"{f}: orphan page, nothing links to it")

    # HubSpot embeds
    for f, p in pages.items():
        scripts = "\n".join(p.scripts) + f.read_text(encoding="utf-8")
        v2_ids = set(re.findall(r"""formId:\s*["']([0-9a-f-]{36})["']""", scripts))
        for fid in p.hs_frames:
            need = HUBSPOT_FORMS.get(fid)
            if need is None:
                warn(f"{f}: HubSpot form {fid} not in HUBSPOT_FORMS; add it with its embed type")
            elif need != "frame":
                fail(f"{f}: form {fid} is a legacy-editor form; hs-form-frame renders it empty, use v2 embed")
            if 'hsforms.net/forms/embed/247447050.js' not in scripts:
                fail(f"{f}: hs-form-frame present but embed/247447050.js script missing")
        for fid in v2_ids:
            need = HUBSPOT_FORMS.get(fid)
            if need is None:
                warn(f"{f}: HubSpot form {fid} not in HUBSPOT_FORMS; add it with its embed type")
            elif need != "v2":
                fail(f"{f}: form {fid} is a new-editor form; use hs-form-frame, not hbspt.forms.create")
            if 'hsforms.net/forms/embed/v2.js' not in scripts:
                fail(f"{f}: hbspt.forms.create present but embed/v2.js script missing")

    # _redirects
    rd = root / "_redirects"
    if rd.is_file():
        for n, line in enumerate(rd.read_text().splitlines(), 1):
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if len(parts) < 2:
                fail(f"_redirects:{n}: expected 'source target [status]'")
                continue
            src, dst = parts[0], parts[1]
            if resolve(root, src) is not None:
                fail(f"_redirects:{n}: {src} still exists as a file, the redirect never fires")
            if dst.startswith("/") and resolve(root, dst) is None:
                fail(f"_redirects:{n}: target {dst} resolves to no file")

    # footer year
    year = str(dt.date.today().year)
    for f in html_files:
        m = re.search(r"©\s*(\d{4})", f.read_text(encoding="utf-8"))
        if m and m.group(1) != year:
            warn(f"{f}: footer says © {m.group(1)}, it is {year}")

    report = {"pages": sorted(str(f) for f in html_files), "failures": failures, "warnings": warnings}
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    ok = not failures
    print(f"site_check: {'OK' if ok else 'FAIL'}, {len(html_files)} pages, "
          f"{len(failures)} failures, {len(warnings)} warnings")
    for x in failures:
        print("  FAIL", x)
    for x in warnings:
        print("  WARN", x)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
