# EE site

Static site for engexcellence.co. `public/` is served by Cloudflare Workers static assets
(`wrangler.jsonc`); every push to `main` deploys through Workers Builds.

## Checks

```
python scripts/site_check.py                 # consistency: em dashes, nav, meta, sitemap, links, HubSpot embeds
python scripts/ui_smoke.py --out ui-smoke    # headless browser at 390 and 1440, screenshots + report.json
```

`site_check.py` needs nothing but Python 3.11+. `ui_smoke.py` needs `pip install playwright &&
playwright install chromium` and network access to hsforms.net for the form checks; pass
`--no-network` in a sandbox. CI runs both on every push and pull request (`.github/workflows/check.yml`);
`check` is the gate, `ui-smoke` is advisory.

## HubSpot forms

Two embed types are in play and they are not interchangeable. New-editor forms use
`<div class="hs-form-frame">` plus `embed/247447050.js`; legacy-editor forms render an empty card
under that embed and need `embed/v2.js` plus `hbspt.forms.create`. `HUBSPOT_FORMS` in
`scripts/site_check.py` records which is which; add any new form there.

## Rules

- No em dashes anywhere. Titles are `Page · EE`.
- Every page carries the same nav, including the Threat Watch link.
- Every public page is in `public/sitemap.xml`; retired pages get a line in `public/_redirects`.
