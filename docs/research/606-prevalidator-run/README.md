# DGII's 606 pre-validator, run on this machine

Task for #217 (Phase 6 map #205), after the static read in #207 (`research/606-prevalidator`). Run 2026-10-22 under the owner's go-ahead recorded on #217: DGII's binary executed only inside a throwaway container with no network, fed only the made-up 606 files in `files/`. Nothing here is a real comprobante; both RNCs are invented numbers with a valid mod 11 digit (python-stdnum 2.2).

## Answer in short

- **It runs on Linux under Mono 6.12, offline.** The extracted `DGII.PreValidado.exe` opens its real form on Xvfb inside `mono:6.12` plus `mono-vbnc` (for `Microsoft.VisualBasic`). Wine was not needed, so it was not tried.
- **The verdict reads mechanically.** `Harness.cs` loads the exe in process, fills the form's own controls, sets the stream the file dialog would set, presses Verificar, waits for the status label and prints the error count and the error list. No screen coordinates, no clicking.
- **Pass:** error count blank and the error box empty. **Fail:** error count `N` and numbered lines such as `1. Detalle:Linea: 1:NCF Invalido !`. The status text reads `Proceso Completado` on both, so it is not the pass signal.
- **No network needed:** every run had `--network none` and finished. Whether it ever tries a socket was not traced.
- **It runs in CI the same way**: a Linux runner with Docker, the image below and Xvfb. Not tried on a runner.

## Obtaining it

- `Pre_Validado.zip` from the URL in #207. DGII answered **403 to curl's default user agent**; a browser user agent plus the pre-validation page as referer returned the file. SHA-256 `130affffbdb3f2f3a6e344564d47f88f1d7381436e7ed8c57cba299504a091e6`, identical to #207's.
- Payload: the cabinet at byte offset 562176 of `Setup_Pre-validado.msi` (`tail -c +562177`), three files: the exe (297,472 bytes), `ICSharpCode.SharpZipLib.dll` (200,704) and the `.exe.config`. The MSI, its .NET 3.5 launch check and `setup.exe` are skipped entirely.
- The binary is DGII's and is not committed here. A CI step would download it and check the hash.

## How to rerun

```sh
docker build -t prevalidador-mono .          # Dockerfile in this folder
docker run --rm --network none \
  -v <payload dir>:/app:ro -v $PWD/files:/files:ro -v $PWD:/harness:ro -v <out>:/out \
  prevalidador-mono /harness/run.sh 130999996 202609 good-v1.txt bad-v1.txt
```

`run.sh` compiles the harness with `mcs`, then per file writes `/out/<file>.log` (the form's verdict) and `/out/<file>.png` (the screen).

## Results

Every file below is `good-v1.txt` with one change, except `bad-v1.txt` (two). Typed RNC `130999996`, period `202609`.

| File | Change | Errors | Message |
| --- | --- | --- | --- |
| `good-v1.txt` | none: header `606\|130999996\|202609\|1`, CRLF, one 23-field line, no trailing newline | 0 | |
| `good-lf.txt` | LF line ending | 0 | |
| `good-trailing.txt` | trailing CRLF | 0 | |
| `good-ecf.txt` | NCF `E310000000001` | 0 | |
| `bad-v1.txt` | header count 2 for 1 line; ITBIS retenido 500 above facturado 180, no payment date | 3 | `Si tiene ITBIS Retenido debe Especificar la Fecha de Pago`; `El ITBIS Retenido no puede ser Mayor que el ITBIS Facturado`; `Encabezado:Cantidad de Registros en el Archivo:(2), NO Coincide con la Cantidad Calculada:(1).` |
| `m-fields22.txt` | 22 fields | 1 | `la cantidad de campos enviados:(22), No es la Requerida : 23` |
| `m-ncf.txt` | NCF `B01000001` | 1 | `NCF Invalido !` |
| `m-period.txt` | header period 202608 | 2 | period typed vs file; comprobante date after the period |
| `m-sum.txt` | total 1100 for 0 + 1000 | 2 | line sum and file total |
| `m-tipoid.txt` | Tipo Id 2 (cedula) on a 9-digit RNC | 1 | `El Rnc: (130999988), NO es válido para el Tipo de Identificación (2)` |
| `m-rnc.txt` | supplier RNC check digit wrong (`130999989`) | **0** | passes |
| `m-hdr-rnc.txt` | reporter RNC check digit wrong (`130999997`, typed the same) | **0** | passes |

Facts for the 606 export:

- A detail line has exactly 23 fields in the instructivo's column order, computed columns (total, ITBIS por adelantar) included; `good-v1.txt` is the minimal passing shape.
- CRLF or LF, with or without a trailing newline, all pass.
- **No RNC check digit is checked**, reporter or supplier; only the identifier's length against Tipo Id. The engine's stdnum gate (#216) is stricter than the pre-validator, so passing the pre-validator says nothing about RNC validity.

## The error file

`Informacion.txt` is written only when Ver Errores is pressed (or on a crash), at `Application.StartupPath + "\Informacion.txt"`, then opened with `Process.Start`. Under Mono the Windows separator makes that path unwritable (`UnauthorizedAccessException` on `/tmp\Informacion.txt`), so the harness reads the same lines from the form's error box instead. It holds `El Archivo Contiene Errores`, the date, `Error(es): N` and the list.

## Limits

- **Mono is not Windows.** The validation is the same IL, but a pass under Mono has not been compared against a pass on Windows. On screen, Guardar reports visible on a pass yet does not paint under Mono.
- **The harness reads private members** (`cbFormato`, `txtRnc`, `txtPeriodo`, `txtpath`, `strm`, `btnVerified`, `lblDetalleBarra`, `lblErrores`, `txtError`, `btnGuardar`). A new DGII build can rename them; pin the hash and fail loudly when it changes.
- Not tried: accented text in fields (the console printed `?` for `ñ` and `í`, a console encoding matter), more than one line, every retention rule, the totals boxes (hidden for 606).
- Server-side checks (RNC status, NCF authorized, duplicates against earlier filings) stay outside, per #207.

## Versions

Mono 6.12.0.182 (`mono:6.12` image, Debian buster archive, `mono-vbnc`, `libgdiplus`, `xvfb`), Docker 29.9.1, pre-validator MSI 2.6 / exe assembly 4.0.0.9 (published 2026-07-31), python-stdnum 2.2 for the made-up RNCs.
