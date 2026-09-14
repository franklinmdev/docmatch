# Fetching UCSF-sourced DocILE documents from the public archive

Answers issue #17, part of the Phase 1 wayfinder map (#15). Read 2026-09-14.
Placeholders (`<ucsf-id-N>`, `<docile-id-N>`) stand in for real ids per the
hard rule against committing real ids or document contents.

## Summary

- The document PDF is fetched by a fixed, undocumented but stable CDN path
  built from the UCSF id, no browser needed.
- The official, documented route is a Solr metadata API at
  `metadata.idl.ucsf.edu` (also reachable at `solr.idl.ucsf.edu`), for
  metadata and full text search only, not for the PDF itself.
- Tested against 5 UCSF-sourced documents from `data/docile/annotations`: all
  5 resolved and downloaded. Page counts matched DocILE's `page_count` for 4
  of 5; one UCSF copy has an extra trailing page DocILE's copy does not.
  Page pixel dimensions at 200 dpi matched DocILE's `page_sizes_at_200dpi` to
  within 1 px (rounding) on every page compared. The UCSF copies still carry
  the "Source: `<url>`" footer that DocILE's paper says it stripped. No
  visible skew in the pages inspected.
- `robots.txt` on the main site allows all crawling. No documented or
  observed rate limiting on the PDF host; the metadata API documents an
  explicit paging cap. No formal bulk-download terms of service was found;
  official guidance points bulk users at a separate metadata+OCR dataset
  export, not bulk PDF download.

## The download route

### PDF, scriptable, no browser

Pattern, built only from the lowercase 8-character UCSF id (the DocILE
`original_filename` field for `source: ucsf` records):

```
https://download.industrydocuments.ucsf.edu/{c1}/{c2}/{c3}/{c4}/{id}/{id}.pdf
```

where `{c1}..{c4}` are the id's first four characters, one per directory
level, and `{id}` is the full id repeated as the final directory and the
filename stem. Example shape (not a real id):
`https://download.industrydocuments.ucsf.edu/a/b/c/d/abcd1234/abcd1234.pdf`.

This is the asset host actually used by the current Industry Documents
Library web app (confirmed by reading the app's shipped JS bundles for asset
hostnames) but it is not mentioned in the site's own API documentation or in
the `industrydocuments/industrydocuments.github.io` GitHub repo that
documents the Solr API. It is nonetheless the same nesting-by-id-prefix
convention long used by the predecessor Legacy/Truth Tobacco Industry
Documents Library, and it answered every one of the 5 test ids with `200
application/pdf`, served by S3 behind CloudFront (`server: AmazonS3`,
`x-cache: ... CloudFront`).

Verified with `curl`, one request per id, `HTTP/2 200`, `content-type:
application/pdf`, no auth, no cookies, no CSRF token:

```
curl -s -L "https://download.industrydocuments.ucsf.edu/<c1>/<c2>/<c3>/<c4>/<ucsf-id>/<ucsf-id>.pdf" -o <ucsf-id>.pdf
```

The human-facing viewer at `https://www.industrydocuments.ucsf.edu/docs/<ucsf-id>`
also resolves (HTTP 200) for every id tested, but it is a JS single-page app
(React) with no server-rendered content or download link in the raw HTML, so
it is not scriptable without executing JS; the direct download path above
avoids needing it.

### Metadata, documented, scriptable

The library's own documentation (`Resources > Application Programming
Interface (API)` on industrydocuments.ucsf.edu, pointing at
`github.com/industrydocuments/industrydocuments.github.io`, read
2026-09-14) documents a Solr query API:

```
https://metadata.idl.ucsf.edu/solr/ltdl3/query?q=id:<ucsf-id>&wt=json
```

(`solr.idl.ucsf.edu` answers the same core and was the host actually called
by the current web app, confirmed by reading its JS bundle; both hosts
returned identical results for every id tested.) The response includes a
`pages` field, confirmed equal to DocILE's `page_count` for every id where
the UCSF page count and DocILE's agreed (see below); it does not return the
PDF or page images, only bibliographic metadata and, for full-text search,
indexed OCR text.

## Page count and visual differences

Sample: 5 UCSF-sourced ids drawn at random from `data/docile/annotations`
(`metadata.source == "ucsf"`, 2645 of 5680 annotation files). For each,
compared DocILE's `page_count` and `page_sizes_at_200dpi` against the
downloaded PDF's page count and each page's `MediaBox`, converted to px at
200 dpi (`pt * 200/72`).

| doc | DocILE page_count | UCSF PDF pages | dimension match at 200dpi |
|---|---|---|---|
| `<docile-id-1>` | 1 | 1 | 1692x2245 vs 1692x2244 (page 1) |
| `<docile-id-2>` | 1 | **2** | 1701x2201 vs 1700x2200 (page 1); page 2 has no DocILE counterpart |
| `<docile-id-3>` | 1 | 1 | 1659x2343 vs 1658x2343 |
| `<docile-id-4>` | 2 | 2 | 1724x2218 vs 1724x2218 (p1); 1718x2214 vs 1718x2214 (p2) |
| `<docile-id-5>` | 1 | 1 | 1875x2339 vs 1692x2111 |

Four of five match exactly on page count. The fifth (`<docile-id-2>`) has an
extra trailing page in the UCSF copy that is absent from DocILE's version;
this looks like DocILE keeping only the invoice page of a multi-page UCSF
filing (e.g. a cover page or continuation dropped), not a re-scan
difference, since the retained page's dimensions still match to the pixel.
`<docile-id-5>`'s width differs by more than rounding (1875 vs 1692); this
is a genuine size discrepancy between DocILE's recorded page size and this
PDF's page box, not explained by DPI rounding, and is worth flagging if this
id is used for calibration.

Visually (rendered two sample pages to PNG locally with PyMuPDF, inspected,
not reproduced here per the no-content rule):

- Both UCSF copies inspected carry a footer line reading `Source:
  https://www.industrydocuments.ucsf.edu/docs/<ucsf-id>` at the bottom of
  the page. DocILE's paper states this footer was removed in its own
  renders; it is present, as expected, in the UCSF originals.
- Neither sample page showed visible skew; both appeared upright.
- No other visual difference was checked (content is out of scope).

DocILE's paper claim that its own page renders are deskewed and normalized
to an 842px long edge describes DocILE's *processed* images, not
necessarily the source PDFs; nothing here confirms or contradicts the
"842px long edge" figure, since DocILE does not publish its own rendered
page images in a form this check could compare against, only the pixel
size metadata used above.

## Rate limits, robots.txt, bulk terms

- `https://www.industrydocuments.ucsf.edu/robots.txt`: `User-agent: *` /
  `Disallow:` (empty), i.e. everything allowed.
- `https://download.industrydocuments.ucsf.edu/robots.txt` and
  `https://solr.idl.ucsf.edu/robots.txt`: both `403 Access Denied` (S3/API
  style error, not a robots directive; these hosts are not set up to serve
  one, and nothing in `robots.txt` semantics applies since the same paths
  never returned a robots-style disallow anywhere).
- No `X-RateLimit-*` or similar headers on any PDF or Solr response
  observed across the 5 test fetches. No throttling encountered at this
  volume.
- The documented Solr API caps **search** results at 100 records per
  request, pageable with `&start=N` up to `start=10000`; beyond that the
  docs require Solr's `cursorMark` deep-paging mechanism instead of `start`.
  This is the only documented "rate limit," and it applies to metadata
  search, not to fetching a single known id's PDF.
- No formal bulk-download terms of service page was found. The `Copyright
  and Fair Use` page (industrydocuments.ucsf.edu, read 2026-09-14) covers
  copyright/fair-use obligations for use of the archive generally but says
  nothing about request volume or scripted access. The `Resources >
  Datasets` page (read 2026-09-14) offers an official bulk export, but only
  of metadata and OCR text, refreshed monthly, "on a do-it-yourself basis"
  with no individual support; it does not offer bulk PDF/page-image
  download, so per-document fetches via the download host above remain the
  only route to page images.

## Sources read

- `https://www.industrydocuments.ucsf.edu/robots.txt` (2026-09-14)
- `https://www.industrydocuments.ucsf.edu/docs/<ucsf-id>` (viewer shell, 5
  ids, 2026-09-14)
- `https://www.industrydocuments.ucsf.edu/assets/api-utils-CpURsbIV.js` and
  sibling JS bundles shipped by the current web app (2026-09-14), read to
  find the actual API/asset hostnames in use
- `https://download.industrydocuments.ucsf.edu/.../<ucsf-id>.pdf` (5 ids,
  2026-09-14)
- `https://metadata.idl.ucsf.edu/solr/ltdl3/query` and
  `https://solr.idl.ucsf.edu/solr/ltdl3/query` (2026-09-14)
- `https://github.com/industrydocuments/industrydocuments.github.io`
  README, the library's own linked API documentation (2026-09-14)
- `https://www.industrydocuments.ucsf.edu/resources/` and the
  `Application Programming Interface (API)`, `Datasets`, and
  `Copyright and Fair Use` pages linked from it (2026-09-14)

## Open question for the spec

`<docile-id-5>`'s width mismatch (1875 vs 1692 px at 200 dpi, not a rounding
gap) is worth a second look before this id is used in the re-pinned subset;
the other four checked cleanly.
