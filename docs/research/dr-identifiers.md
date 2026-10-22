# Dominican identifiers: python-stdnum, dgii-ts and DGII's bulk RNC file

Research ticket #210 (Phase 6 map #205). Read 2026-10-22. Branch `research/dr-identifiers`, never merged.

Sources, with the version read:

- python-stdnum 2.2, uploaded to PyPI 2026-01-04 (https://pypi.org/pypi/python-stdnum/json, read 2026-10-22). Source read from the installed wheel, modules `stdnum/do/rnc.py`, `cedula.py`, `ncf.py`, `__init__.py`, and `stdnum/util.py` (`get_soap_client`).
- `dgii-ts` 0.3.0, local checkout `/home/franklinmdev/dgii-ts` at `9088f2c` (2026-09-22): `src/validators/*.ts`, `src/bulk/*.ts`, `README.en.md`.
- DGII pages and the file itself, fetched 2026-10-22.

## Short answer

| Identifier | python-stdnum 2.2 | dgii-ts 0.3.0 |
|---|---|---|
| RNC (9 digits) | yes, mod 11 plus a 23-entry whitelist | yes, same algorithm, same whitelist, plus a leading-zero rule |
| Cedula (11 digits) | yes, Luhn plus a 578-entry whitelist | yes, same, same whitelist, plus a `000` prefix rule |
| NCF (B series, 11 chars) | yes, shape and type code only | yes, shape and type code only |
| NCF, legacy 19 chars (A or P series) | yes | no |
| e-NCF (E series, 13 chars) | yes, shape and type code only | yes, shape and type code only |

Nothing validates a sequence number against DGII's authorization ranges offline. Whether a given NCF or e-NCF was issued needs DGII's web form (see "Online lookups").

## python-stdnum

Source: `stdnum/do/rnc.py`, `cedula.py`, `ncf.py` in the 2.2 wheel (read 2026-10-22). The package `stdnum.do` has exactly these three modules plus `__init__.py`. Its funding note says development was funded by iterativo.do.

### RNC (`stdnum.do.rnc`)

- `compact`: strips spaces and hyphens. `validate` raises `InvalidFormat` for any non-digit, returns early for whitelisted numbers, then `InvalidLength` unless 9 digits, then `InvalidChecksum`.
- Check digit: weights (7, 9, 8, 6, 5, 4, 3, 2) over the first 8 digits, sum mod 11, digit is `(10 - sum%11) % 9 + 1`. That gives 2 when the remainder is 0, 1 when it is 1, else `11 - remainder`.
- Whitelist: 23 numbers that fail the checksum but are real (22 of 9 digits, one of 8 digits, `10233317`). The 8-digit one passes only because the whitelist check precedes the length check.
- No rule on the first digit.

### Cedula (`stdnum.do.cedula`)

- Same `compact`. Whitelist first, then `InvalidLength` unless 11 digits, then `stdnum.luhn.validate` (plain Luhn).
- Whitelist: 578 numbers (576 of 11 digits, 2 of 10 digits, `0094662667` and `0710208838`). These are individual people's identifiers published in a public source file. Do not copy them into docmatch fixtures or docs.
- No prefix rule.

### NCF and e-NCF (`stdnum.do.ncf`)

One `validate` for three shapes, decided by length after `compact` (strips spaces, uppercases):

| Length | Prefix | Layout checked | Type codes allowed |
|---|---|---|---|
| 13 | `E` | E, then 12 digits; type is chars 1 to 2 | 31, 32, 33, 34, 41, 43, 44, 45, 46, 47 |
| 11 | `B` | B, then 10 digits; type is chars 1 to 2 | 01, 02, 03, 04, 11, 12, 13, 14, 15, 16, 17 |
| 19 | `A` or `P` | letter, then 18 digits; type is chars 9 to 10 | same as the 11-char list |

- No checksum exists for any of them. The check is shape plus an allowed type code. Errors: `InvalidLength`, `InvalidFormat`, `InvalidComponent`.
- The code comments label codes 31 to 47 as the e-CF types (no 42; the code has none).
- Documentation bug in 2.2: the module docstring says the 11-char number has "a 7-digit serial" and the 13-char e-CF "an 8-digit serial". The code accepts an 8-digit serial (B + 2 + 8 = 11) and a 10-digit serial (E + 2 + 10 = 13). The code matches dgii-ts and the ticket's "E-series, 13 chars"; the docstring does not.
- The docstring cites DGII's Norma 05-19 (https://dgii.gov.do/workshopProveedoresTI-eCE/Documents/Norma05-19.pdf) and the receipt-types page. I did not read either (unverified here).
- The module says the 11-char format is in force since 2018-05-01 and the 13-char one since 2019-04-08 (docstring, not independently checked).

## Online lookups in python-stdnum

All three are marked `# pragma: no cover` and are not run by the test suite.

| Function | Hits | Method | Status read 2026-10-22 |
|---|---|---|---|
| `rnc.check_dgii`, `rnc.search_dgii`, `cedula.check_dgii` (a thin wrapper over the RNC one) | `https://www.dgii.gov.do/wsMovilDGII/WSMovilDGII.asmx?WSDL`, SOAP method `GetContribuyentes` (search by number or by name) | needs zeep, suds or PySimpleSOAP | Dead. A GET on the WSDL URL answers 301 to `https://dgii.gov.do/paginas/default.aspx?WSDL`, which serves the HTML home page (200, `text/html`), not a WSDL. Consistent with dgii-ts README: "Permanently blocked by DGII since January 2025" (`README.en.md`, lines 26 and 55). I did not run the call itself; the evidence is that no WSDL is served. |
| `ncf.check_dgii` | `https://dgii.gov.do/app/WebApps/ConsultasWeb2/ConsultasWeb/consultas/ncf.aspx`, an ASP.NET form scraped with requests and lxml (GET for `__VIEWSTATE` and `__EVENTVALIDATION`, then POST) | for E-series it also needs the buyer RNC and the 6-character security code | Page answers 200 and still has the form fields the code posts (`txtNCF`, `txtRNC`, `txtRncComprador`, `txtCodigoSeg`, `btnConsultar`). Whether a POST still returns a result block that the code's selectors parse: unverified, I did not submit a query. |

Returned fields for the SOAP lookup: rnc, name, commercial_name, status (1 inactive, 2 active), category, payment_regime (1 N/D, 2 NORMAL, 3 PST). For `ncf.check_dgii`, an NCF returns name, status (for example `VIGENTE`), type and a validation message; an e-NCF returns status, issuing and buyer RNC, security code, dates, total and ITBIS total (docstring of `ncf.py`).

Consequence for docmatch: the only live, free source for RNC data is the bulk file below. The NCF validity page is a per-document web form, not an API, and DGII's terms (below) limit use to non-commercial purposes.

## Differences from dgii-ts

Sources: `src/validators/{rnc,cedula,ncf,ecf}.ts`, whitelist files, `src/bulk/*` in the local checkout (read 2026-10-22).

- Algorithms are identical. dgii-ts RNC check digit is the same formula written out (remainder 0 gives 2, 1 gives 1, else 11 minus remainder). Cedula Luhn is the same.
- Whitelists are the same sets: I parsed both and compared them, 23 RNC and 578 cedula entries, no difference either way. The dgii-ts file comments say "Fuente: python-stdnum", so it is a copy, and will drift if stdnum adds entries.
- dgii-ts is stricter in two places stdnum is not: an RNC starting with `0` is rejected, and a cedula starting with `000` is rejected (both after the whitelist, so whitelisted numbers still pass; several whitelisted cedulas start with `000`).
- Input handling differs: dgii-ts strips every non-digit (`stripNonDigits`), so `12A3...` is cleaned instead of rejected. stdnum strips only spaces and hyphens and raises `InvalidFormat` on letters.
- dgii-ts returns `{ valid, formatted }` and no reason; stdnum raises a typed exception (`InvalidFormat`, `InvalidLength`, `InvalidChecksum`, `InvalidComponent`), which is more useful for a pipeline that must say why a gate failed.
- NCF: dgii-ts accepts only `B` + 10 digits and gives the type name; it has no 19-char legacy form. e-NCF is a separate function `validateEcf` with the same type list as stdnum (31, 32, 33, 34, 41, 43, 44, 45, 46, 47). The B-series type list is the same 11 codes. stdnum merges both in one function and adds the 19-char form.
- Neither library validates the e-NCF against anything beyond shape and type code.

## DGII_RNC.zip

All facts fetched 2026-10-22 unless noted.

- URL: `https://dgii.gov.do/app/WebApps/Consultas/RNC/DGII_RNC.zip`, linked as "TXT" under "Descargar Listado de todos los RNC" on https://dgii.gov.do/app/WebApps/ConsultasWeb2/ConsultasWeb/consultas/rnc.aspx. The same page links a CSV variant, `.../RNC/RNC_CONTRIBUYENTES.zip` (not opened).
- The URL is the same as `DGII_BULK_URL` in dgii-ts `src/bulk/types.ts`.
- HEAD: HTTP 200, `content-length: 22899217` (about 21.8 MiB), `content-type: application/x-zip-compressed`, `last-modified: Sat, 17 Oct 2026 07:04:08 GMT`, served through Azure Front Door. The CSV zip: 26,566,847 bytes, last-modified Sat, 17 Oct 2026 05:01:05 GMT. A plain `curl` with a browser User-Agent works; the directory URL `.../Consultas/RNC/` answers 403.
- Contents: one entry, `TMP/DGII_RNC.TXT`, 91,204,994 bytes, zip timestamp 2026-10-17 02:00. No header row, no BOM.
- Encoding: not valid UTF-8 (fails at byte 0xD3); latin-1 (ISO-8859-1) decodes cleanly, matching dgii-ts. Line endings CRLF.
- Delimiter: `|`. 795,029 lines; 794,985 have exactly 11 fields. 44 lines do not (counts of fields: 3 for 5 lines, 9 for 16, 2 for 22, 1 for 1), presumably names with embedded line breaks (unverified cause). dgii-ts drops lines that are not 11 fields, so those records are lost silently.
- Columns (no header; mapping from dgii-ts `src/bulk/parser.ts` and checked against the file):

| Index | Meaning (dgii-ts name) | Non-empty rows of 794,985 |
|---|---|---|
| 0 | RNC or cedula (`rnc`) | 794,985 |
| 1 | name or razon social (`nombre`) | 794,985 |
| 2 | commercial name (`nombreComercial`) | 446,782 |
| 3 | economic activity (`actividad`) | 770,863 |
| 4 to 7 | reserved, always empty | 0 |
| 8 | incorporation date, `dd/mm/yyyy` (`fechaConstitucion`) | 727,671 (all match the pattern) |
| 9 | status (`estado`) | 794,985 |
| 10 | regime (`regimen`) | 794,985 |

- Status values and counts: ACTIVO 373,704; SUSPENDIDO 335,012; DADO DE BAJA 74,950; CESE TEMPORAL 9,787; ANULADO 1,256; RECHAZADO 276. This is exactly dgii-ts's `DGII_ESTADOS` vocabulary.
- Regime values: NORMAL 759,436; RST 35,549. PST, which dgii-ts mentions in a comment and stdnum's SOAP docs list, does not occur.
- Key column: no duplicate keys, all digits. Length 9: 503,592 rows (first digit 1: 451,919; 4: 49,878; 5: 1,795); length 11: 291,392 rows; length 8: 1 row.
- Cross-check with python-stdnum 2.2: all 503,592 nine-digit keys pass `rnc.is_valid` (the 8-digit one is the whitelisted `10233317`); of the 291,392 eleven-digit keys, 291,360 pass `cedula.is_valid` and 32 fail. So the file mixes companies and individuals (people who are taxpayers appear under their cedula). That makes the file personal data for about 37 percent of rows. Treat the same way as DocILE: never commit it, never put rows in a prompt or a fixture.
- Not in the file: ITBIS-related fields, address, e-invoicing enrollment, certificate data. Whether an RNC is enrolled as an electronic issuer is not here (unverified where it is published).
- Update cadence: the page and the terms say only "actualizados periodicamente" (the terms) and give no schedule. dgii-ts README calls it "daily" (`README.en.md`, line 28, and "daily taxpayer ZIP file"), but that is the library's claim, not DGII's. Observed: both files stamped 2026-10-17 (a Saturday) early morning, five days before the read, so a daily refresh is not confirmed by one observation. Cadence: unverified. A repeat HEAD on later days would settle it.
- Terms of use: https://dgii.gov.do/Paginas/TerminosUso.aspx, read 2026-10-22. Relevant clauses (translated): DGII permits downloading and copying content for non-commercial use, with no right to sell, distribute, compile or create derivative works; the downloadable files, "such as the RNC database", are "updated periodically" and "published without commercial purposes"; for commercial use, contact 809-689-3444 or informacion@dgii.gov.do; content is public except what owners reach through the Oficina Virtual; no warranty on accuracy. There is no separate license text for the file and no named open-data license (none found on the pages read).
- Implication: a portfolio engine that reads the file locally for non-commercial work fits the terms. Redistribution of the file, a derived registry shipped in a product, or a commercial service built on it would need DGII's permission. Whether a derived sample counts as a "derivative work" is a legal reading I did not settle; marked unverified.

## What I could not pin

- Whether `ncf.check_dgii` still parses DGII's result page (form fields exist; no POST sent).
- Update cadence of the zip.
- Contents of Norma 05-19 and the receipt-types page that stdnum cites; whether code 42 or other E-series types exist today.
- The cause of the 44 malformed lines.
- Whether DGII publishes e-invoicing enrollment per RNC anywhere downloadable.
- Legal status of derived samples under the terms.
