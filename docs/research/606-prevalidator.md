# DGII 606 pre-validator: where it comes from and whether it can run here

Research for ticket #207 (Phase 6 map #205). Date read for every claim: 2026-10-22 unless a line says otherwise. Machine: `Linux omarchy 7.2.8-9-omarchy x86_64` (Arch), no Wine, no Mono, no `olevba`; Docker 29.9.1 and a .NET (Core) 10.0.13 runtime are installed.

Method: the tool was downloaded into empty directories under `/tmp/claude-1000/dl606/` and inspected statically (`file`, `unzip -l`, `strings`, a CAB carve with `bsdtar`). Nothing downloaded was executed. Nothing downloaded is committed.

Prior work checked for drift: `franklinmdev/recibox` `docs/research/dgii-606-607-608-export-spec.md` (researched 2026-06-04), cited below as "recibox".

## Answer in short

- The tool is a **Windows installer for a .NET Framework WinForms program**, not an Excel macro. It is a GUI with a file picker and a "Verificar" button. No command-line mode was found in the binary.
- It is **not runnable on this machine as is**. No Wine, Mono or Windows is installed, and its installer requires .NET Framework 3.5. Wine or a Windows VM are the options. Neither was tried (rule: do not execute downloaded binaries). Both are therefore **unverified**.
- It is **not unattended-friendly**: GUI only. Unattended use would mean GUI automation or reimplementing its checks.
- It checks **structure and per-line rules locally**. The checks that matter most for a real filing (supplier RNC status, NCF authorized, duplicates) run **only server-side** in the Oficina Virtual (OFV), per DGII's 606 instructivo.
- Drift since recibox: the tool was **republished 2026-07-31** (recibox read an earlier build) and the 606 instructivo is now the **February 2026** edition. The 606 layout itself shows no change in what was checked (see Drift).

## 1. Where it is downloaded

- Page: "Herramientas de Pre-validacion", <https://dgii.gov.do/herramientas/formularios/pre-validacion/Paginas/default.aspx>, HTTP 200, read 2026-10-22.
- The page lists five tools. The relevant row: "Herramienta de Pre-Validacion de Envio de Datos", "Modificado: 31/07/2026 - Tamano: 629,5KB".
- Download link on the page: <https://dgii.gov.do/herramientas/formularios/pre-validacion/Documents/Pre_Validado.zip>. Response headers, read 2026-10-22: `last-modified: Fri, 31 Jul 2026 14:09:08 GMT`, `content-length: 644579`, `content-type: application/x-zip-compressed`, SharePoint-hosted (`microsoftsharepointteamservices: 16.0.0.10417`), no login. SHA-256 of the file as downloaded: `130affffbdb3f2f3a6e344564d47f88f1d7381436e7ed8c57cba299504a091e6`.
- The other four tools on that page (Validador.zip for the sales-book solution, Norma 07-2019, Norma 13-2011, Formato 120) are not the 606 tool.
- The 606 filling template is a separate download: `Formato-de-Envio-606-(NG-07-2018-y-05-2019).zip` from the index <https://dgii.gov.do/cicloContribuyente/obligacionesTributarias/remisionInformacion/Paginas/formatoEnvioDatos.aspx> (read 2026-10-22). It contains one legacy `.xls` (1,684,992 bytes, dated 2025-06-04 in the zip) plus the instructivo PDF; `last-modified: Tue, 28 Oct 2025`. That is the Excel-with-macros generator recibox mined. It is not the pre-validator.

## 2. Version and what is inside

`unzip -l Pre_Validado.zip` (read 2026-10-22):

| File | Bytes | Date in zip |
| --- | --- | --- |
| `Prevalidador/setup.exe` | 530,432 | 2026-07-21 |
| `Prevalidador/Setup_Pre-validado.msi` | 749,568 | 2026-07-21 |

`file`: `setup.exe` is "PE32 executable for MS Windows 5.01 (GUI), Intel i386" (a Visual Studio bootstrapper; its strings include "Installing VSTO solution via vstoee", which is generic bootstrapper text and not evidence the app is an Office add-in). The MSI is a Windows Installer package, last saved 2026-07-21, title "Sistema de Pre-Validacion", author "Direccion General de Impuestos Internos".

Strings in the MSI tables (static read):

- `ProductName` Sistema_Prevalidacion, `ProductVersion` **2.6**, Manufacturer DGII.
- Launch condition: `VSDFrameworkVersion` **v3.5**, `VSDAllowLaterFrameworkVersions` False in the table dump (unverified how the check reads that flag). The install aborts with a Microsoft fwlink if .NET Framework 3.5 is absent.
- Payload CAB carved out of the MSI (offset 562176, 3 files): `DGII.PreValidado.exe` (PE32 .NET assembly, **assembly version 4.0.0.9**), `ICSharpCode.SharpZipLib.dll` (0.86.0.518), and `DGII.PreValidado.exe.config`.
- `DGII.PreValidado.exe.config`: `<supportedRuntime version="v2.0.50727"/>`, i.e. the CLR 2.0 line (.NET 2.0 to 3.5). References `Microsoft.VisualBasic` and `System.Windows.Forms`: a VB.NET WinForms app.

So: current version "installer 2.6 / exe 4.0.0.9, published 2026-07-31". The page and zip carry no human-readable version label other than these internals.

## 3. Platform it needs

- Windows with .NET Framework 3.5 (the MSI launch condition). On Windows 10/11 that is an optional feature; this was not source-checked here (unverified).
- Not an Excel macro, no Office dependency found: no VBA, no Office interop strings in the assembly. LibreOffice is therefore irrelevant to this tool. (LibreOffice is relevant only to the separate 606 `.xls` generator template, which was not tested here.)
- Not runnable under the .NET 10 runtime installed here: the assembly targets the .NET Framework CLR 2.0 and uses WinForms (assertion from the config file; that modern .NET on Linux cannot host Framework WinForms assemblies is general knowledge, unverified against a source in this session).

## 4. Options to run it on Linux or in CI

None was executed. Evidence per option:

| Option | Evidence | Verdict |
| --- | --- | --- |
| Wine + winetricks `dotnet35` | Not tested. The WineHQ wiki and AppDB pages could not be fetched (Anubis access denied, 403) so no Wine-side evidence was read. | **unverified** |
| Wine + wine-mono | wine-mono README (<https://raw.githubusercontent.com/madewokherd/wine-mono/main/README.md>, read 2026-10-22): "intended as a replacement for the .NET Framework (4.8.1 and earlier) in Wine"; also says "The winforms and wpf projects are not being updated from upstream and have diverged significantly". Whether the MSI's v3.5 launch condition passes under wine-mono is **unverified**, and so is whether this app's WinForms controls render and run. | **unverified** |
| Mono directly (skip the MSI) | Mono WinForms page (<https://www.mono-project.com/docs/gui/winforms/>, read 2026-10-22): "Support for Windows Forms 2.0 is complete"; lists X11, Win32 and macOS drivers. The exe is a 32-bit CLR 2.0 assembly built for WinForms 2.0/3.5, so `mono DGII.PreValidado.exe` is plausible after extracting the CAB. Not tested; Mono is not installed here. | **unverified, most promising to try** |
| Windows VM or Windows CI runner | The tool installs and runs by design on Windows (it is DGII's published path, instructivo 606 p.11 to 13). A GitHub Actions `windows-latest` runner was not checked for .NET 3.5 availability. | works by design; CI availability **unverified** |
| LibreOffice macros | Does not apply (see section 3). | not applicable |

## 5. Unattended use

- Strings show a file dialog (`ASCII files (*.txt)|*.txt`, "Seleccionar Archivo a Validar"), input boxes (`txtRnc`, `txtPeriodo`, `txtPeriodoHasta`, ITBIS and amount boxes for some formats), a format drop-down, and buttons "Verificar", "Guardar", "Ver Errores".
- No command-line argument handling was found (`CommandLine`/`GetCommandLineArgs` absent from the assembly's strings). Unverified by running it.
- The instructivo describes it as an interactive tool: pick format, fill the requested fields, attach the file, press "Verificar" (<https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/4-LlenadoyEnvioFormato606.pdf>, February 2026 edition, `last-modified: Thu, 12 Feb 2026`, pp.10 to 13 of that PDF).
- Conclusion: **unattended means GUI automation** (for example xdotool/Xvfb under Wine or Mono, or AutoHotkey on Windows), which is brittle. The alternative is a deterministic reimplementation of the same checks in code (the checks are enumerable from the assembly strings, section 7), validated against the tool by hand on a Windows machine. Neither was built; this is a design note, not a finding.

## 6. Pass and failure outputs

From the 606 instructivo (February 2026, same URL as above, pp.12 to 13) and the assembly strings:

- Failure: errors "se mostrara en la seccion Errores"; the user fixes the file and presses "Verificar" again. Strings: "El Archivo Contiene Errores", "Ver Errores", detail lines of the form `Detalle: Linea: {0} ...`, and an error text file path ending `\Informacion.txt` (where it is written and when is **unverified**).
- Pass: "Cuando no existan errores, la herramienta le dara la opcion de Guardar" (instructivo p.13). That option only compresses the text file; the plain `.txt` is what gets uploaded. The string "Proceso Completado" is present in the assembly and is probably the pass message. Unverified: the exact on-screen text on a pass, and any exit code or file the tool writes. A pass therefore cannot be read mechanically from a known output; this needs an observed run on Windows or a working Wine/Mono run.
- A pass here is **not** acceptance. Real acceptance is OFV status "Completado" versus "Error" under "Consulta Envios 606" (instructivo pp.18 to 19), which also runs the server-side checks listed there: inactive or blocked supplier RNC, NCF not authorized or eliminated, NCF already reported, credit note's modified NCF not yet reported, a Type B NCF from an issuer obliged to issue e-CF, and e-CF sender/receiver mismatches.

## 7. What the pre-validator checks (static, from strings; none observed live)

Local, file-only checks suggested by the message strings, unverified until run:

- Header: field count, information code matches the chosen format, RNC matches the typed RNC, period matches the typed period, record count matches the number of lines, file not empty.
- Per-line: NCF and e-NCF structure ("NCF Invalido !", "e-NCF Invalido !"), modified-NCF required for credit/debit notes, dates not blank, a tipo-retencion rule set (for example goods-and-services type 02 required, NCF type B11 or E41 required for a given retention), propina legal amount, and the consumo-invoice detail threshold (strings carry both RD$250,000.00 and RD$50,000.00).
- Format drop-down options: 606, 607, 608, 612, 613, 646. Some formats only allow pre-validating a given period ("Para este formato solo es permitido pre validar desde el periodo ...").
- Network: the assembly contains the VB `My.WebServices` boilerplate (`SoapHttpClientProtocol`) but no URL, .asmx or endpoint string was found, so no evidence it calls DGII. Unverified at runtime.

## 8. Drift since recibox (2026-06-04)

| Item | recibox (2026-06-04) | Now (2026-10-22) | Source |
| --- | --- | --- | --- |
| Pre-validator page and purpose | live, covers 606/607/608, "same engine the OFV runs" | live; tool republished **2026-07-31** (zip internals dated 2026-07-21), after recibox. The "same engine the OFV runs" claim is **not supported**: the instructivo puts the RNC/NCF status checks server-side only. | pre-validacion page; instructivo pp.18 to 25 |
| Platform and form | not stated | .NET Framework 3.5 WinForms installer (MSI), not Excel | section 2 |
| 606 instructivo edition | "Julio 2025" | **Febrero 2026** (title page; `last-modified` 2026-02-12). Pre-validation section and OFV alert list read; this session did not re-diff all 23 columns. Column layout drift is **unverified**. | instructivo |
| 606 envio template zip | NG-07-2018-y-05-2019 package | same package name; zip `last-modified` 2025-10-28 (predates recibox) | index page and header |
| Guia Informativa | Mayo 2026 | `last-modified` 2026-05-12, unchanged | header |
| Governing norm | NG 07-2018 + 05-2019 | still the package name; no newer norm seen on the index page. Not independently searched for a 2026 norma. | index page |
| e-CF alerts in OFV | "e-CF received must be reported in the 606" | instructivo adds explicit OFV alerts: Type B NCF from an issuer obliged to issue e-CF, and e-CF issuer/receiver mismatches (p.23 to 24, citing Ley 32-23 and Decreto 587-24) | instructivo |

Recibox conclusion "the pre-validator is the tool to test output against" still holds as a manual step. Its "same engine as the OFV" line should be dropped.

## 9. Consequences for #205 and #215

- A mechanical pass signal from DGII's own tool is **not available** today: GUI only, pass text unobserved, runtime on Linux untested.
- Cheapest next experiment (needs Franklin's go, since it executes the downloaded binary): extract the CAB payload and run `DGII.PreValidado.exe` under Mono or Wine in a throwaway container, feed it a known-good and a known-bad 606 file, and record the exact pass text, error text and `Informacion.txt` behaviour. Alternatively a one-time manual run on any Windows machine to capture screenshots and the error file, then pin those in tests.
- Unverified list: Wine/Mono runnability; the exact pass message; exit code or output file; command-line mode; whether any network call happens; whether .NET 3.5 is enabled on Windows CI runners; 606 column drift between the July 2025 and February 2026 instructivo.

## Sources (all read 2026-10-22)

- <https://dgii.gov.do/herramientas/formularios/pre-validacion/Paginas/default.aspx>
- <https://dgii.gov.do/herramientas/formularios/pre-validacion/Documents/Pre_Validado.zip>
- <https://dgii.gov.do/cicloContribuyente/obligacionesTributarias/remisionInformacion/Paginas/formatoEnvioDatos.aspx>
- <https://dgii.gov.do/herramientas/formularios/formatoEnvioDatos/Documents/Formato%20de%20compras%2c%20bienes%20y%20servicios/Formato-de-Envio-606-%28NG-07-2018-y-05-2019%29.zip>
- <https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/4-LlenadoyEnvioFormato606.pdf>
- <https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/1-Guia-Informativa-sobre-los-Fomatos-Envio-de-Datos.pdf> (headers only)
- <https://raw.githubusercontent.com/madewokherd/wine-mono/main/README.md>
- <https://www.mono-project.com/docs/gui/winforms/>
- Prior work: `franklinmdev/recibox` `docs/research/dgii-606-607-608-export-spec.md` (2026-06-04), private repo.
