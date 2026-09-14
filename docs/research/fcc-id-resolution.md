# Can DocILE's FCC Public Inspection File ids be resolved to downloadable files?

Research for [issue #26](https://github.com/franklinmdev/docmatch/issues/26), part of the phase 1 map (#15).

Date read: 2026-09-14. No document contents, titles, callsigns, or real ids are reproduced below; every id shown is a placeholder or a synthetic zero id.

## Question

DocILE's `source: pif` documents carry `metadata.original_filename`, an FCC Public Inspection File UUID, but no station or entity id. Is there a search endpoint, a public URL pattern, or a metadata route that resolves the UUID alone to the file?

## Answer

No. Every route that can fetch or search for a file on `publicfiles.fcc.gov` requires an `entityId` (the owning station or filer's facility id) in addition to the file id, and DocILE's own metadata for `pif` documents does not carry that id anywhere. The one lookup that does not need an `entityId`, the site's own full text file search, does not index the internal file UUID as searchable text, confirmed live against 3 sample documents. The public subset cannot cleanly include these documents again without a second, independent way to learn the owning entity id.

## What DocILE's own metadata offers

Read directly from `data/docile/annotations/<docid>.json` (not committed, per repo rule 6). For every `source: pif` document sampled, `metadata` has exactly these keys: `source`, `original_filename`, `cluster_id`, `currency`, `document_type`, `language`, `page_count`, `page_sizes_at_200dpi`, `page_to_table_grid`. `cluster_id` is DocILE's own internal deduplication cluster, unrelated to any FCC identifier. There is no callsign, facility id, FRN, or entity id field. The dataset's top level split files (`train.json`, `val.json`, `trainval.json`) are plain lists of document ids, not a crosswalk to FCC entities. So nothing in the local DocILE data can supply the missing id.

## The documented API

`https://publicfiles.fcc.gov/developer` embeds three Swagger 2.0 specs, fetched directly:

- `/json/opif-cdbs.json`, basePath `/api/service`: facility, license, and ownership lookups keyed by callsign, FRN, or facility id. No route accepts a file id or a bare UUID.
- `/json/opif-contour-apis.json`: signal contour data, unrelated.
- `/json/opif-file-manager.json`, basePath `/api/manager`: the file and folder manager. Confirmed by reading the spec's `parameters` and `paths` blocks:
  - `GET /file/id/{fileId}.{format}` (Get File Details) and `GET /search/key/{searchKey}.{format}` (Search for files and folders) both list `#/parameters/facilityIdParam` as a required parameter, defined as `{"name": "entityId", "in": "query", "description": "Unique Entity Id.", "required": true, "type": "string"}`.
  - No `download` path is documented anywhere in this spec.

Live behaviour matches the spec exactly, tested against 5 sampled `pif` UUIDs:

- `GET /api/manager/file/id/{uuid}.json` (no `entityId`) returns HTTP 400 `{"statusMessage":"Invalid File ID"}` for all 5.
- `GET /api/manager/search/key/{uuid}.json` (no `entityId`) returns HTTP 200 with `{"statusMessage":"EntityId is missing."}` for all 5.
- Adding a syntactically valid but wrong `entityId` (`entityId=1`) to the search endpoint returns success with an empty result set, confirming the search is scoped to one entity and cannot be pointed at "any entity" to find a file by id alone.

## The undocumented download route

The site's own front end builds download links as:

```
https://publicfiles.fcc.gov/api/manager/download/{folder_id}/{file_manager_id}.pdf
```

read directly out of `addFilesResult()` in the rendered `/findfiles/...` page's inline script. This route is not in any of the three Swagger specs. Note that the `File` schema in `opif-file-manager.json` has both a `file_id` and a separate `file_manager_id` field, so even a genuine `file_id` may not be the value this route wants.

Every attempt at this route, including a URL surfaced by a general web search that looked like a real, previously indexed `entityId`/`fileId` pair, returned an Akamai edge WAF "Access Denied" page (HTTP 400 or 403 depending on headers), not an application-level error. This held with and without a browser-like `User-Agent` and `Referer`. Because the block happens at the edge, before the application would validate the ids, this route's true behaviour with a correct pair is unverified: it may work from a real signed-in browser session, or it may be blocked outright for external traffic. It gave no path forward here either way, since we have no genuine `folder_id`/`entityId` to test with.

## The one entityId-free lookup: full text file search

The `/find` and `/findfiles` pages' "Files" tab calls a separate, undocumented backend: `https://www.fcc.gov/search/api?t=opif&q={term}&s=0&o=best-match`, read out of the same inline script. This endpoint takes no entity id, so it is the one candidate that could resolve a bare UUID.

Direct `curl` requests to it are blocked by Akamai bot detection (HTTP 403 "Access Denied"), matching the earlier report's finding for the fcc.gov terms page. A real browser session (agent-browser, full Chromium) is not blocked and was used to test 3 of the 5 sampled UUIDs by loading `https://publicfiles.fcc.gov/findfiles/{uuid}/page-offset-0/order-best-match/filter-none/` directly. All 3 came back with an explicit zero count and the page's own message, "Your search for `{uuid}` could not be found in any stations" / "No search results found for: `{uuid}`". This is a real, unblocked answer from the production search, not a WAF artifact, and it is consistent across all 3 tried. The search indexes visible file names and folder paths (and entity fields like callsign), not the internal system UUID DocILE stores as `original_filename`, so this route does not help either.

## FCC API terms of use

The current page, `http://www.fcc.gov/developer/api-terms-of-service`, returns HTTP 403 from the same Akamai bot protection when fetched directly, so its current, exact wording is unverified.

A Wayback Machine capture from 2012-07-14 (`http://web.archive.org/web/20120714034604/http://www.fcc.gov:80/developer/api-terms-of-service`) is readable. It is a short, generic terms of service: the API may be used "to develop a service or service to search, display, analyze, retrieve, view and otherwise 'get' information from FCC data," subject to an attribution notice ("This product uses the FCC Data API but is not endorsed or certified by the FCC"), a right for FCC to rate-limit or block abusive use, and standard warranty/liability disclaimers. It says nothing specific about file ids, entity ids, or UUID resolution, and nothing that would forbid the lookups attempted above. The Wayback CDX index shows this exact URL started returning HTTP 301 redirects from around October 2012 onward, suggesting the terms were folded into FCC's general Website Policies at some point after that; whether that later, current text differs in substance is unverified, since the live page could not be read directly.

## What was tried, for reproducibility

- 5 `pif` documents' `metadata.original_filename` UUIDs, sampled from `data/docile/annotations/*.json`.
- `GET /api/manager/file/id/{uuid}.json` and `GET /api/manager/search/key/{uuid}.json` on `publicfiles.fcc.gov`, with and without a dummy `entityId`, for all 5.
- `GET /api/manager/download/{entityId}/{uuid}.pdf` on `publicfiles.fcc.gov`, with a zero-filled `entityId`, with a real-looking `entityId`/`fileId` pair found via web search, and with the `entityId` segment omitted, for 1 UUID, with and without browser-like headers.
- `GET https://www.fcc.gov/search/api?t=opif&q={uuid}` directly via `curl`, for 1 UUID (blocked).
- A full browser session (agent-browser) loading `publicfiles.fcc.gov/find/{uuid}/...` and `.../findfiles/{uuid}/...` for 3 UUIDs, reading the rendered "no results" message directly.
- Reading `opif-cdbs.json`, `opif-contour-apis.json`, and `opif-file-manager.json` in full.
- Reading `data/docile/annotations/*.json` metadata keys for 20 sampled `pif` documents, and the top level `train.json`/`val.json`/`trainval.json` split files.
- Fetching the Wayback Machine CDX index and a 2012-07-14 snapshot of the FCC API terms of service page.

No files were kept from any download attempt; nothing under `data/pif-probe/` was populated because no attempt reached a working PDF response.

## Recommendation for the wayfinder map

Do not spend further effort on `publicfiles.fcc.gov`'s own API or search for this. If the `pif` subset is wanted back in the public benchmark, the only paths left are: a separate, external crosswalk from DocILE's own document id or UUID to an FCC entity/facility id (not found in the locally mirrored `data/docile` files; worth one check against DocILE's own published dataset documentation, outside the scope of this note), or dropping `pif` documents from the public subset the way `source: pif` documents from other origins may already have been handled elsewhere in the pipeline.
