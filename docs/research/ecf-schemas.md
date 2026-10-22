# What DGII publishes for e-CF E31, E32, E41 and E43

Research for ticket #206, part of map #205. Read 2026-10-22. Primary sources only: dgii.gov.do and ayuda.dgii.gov.do (DGII's own help portal). Anything not pinned to a source is marked "unverified". This branch is never merged.

## Short answer

- DGII publishes the **XSDs** for all four types, the **format specification** (PDF, field by field), the **signing guide**, the **printed-representation models** and the **technical description**. All are on one page.
- DGII does **not** publish sample XML files. No samples section exists on that page, and DGII's own help portal answers two requests for samples without giving one (details below). A public sample set large enough for a fixture does not exist at the primary source. The only sample-like material is the certification test set, an Excel file served inside the certification portal to an enrolled taxpayer.
- Every header and line cell the engine reads has a counterpart except a handful (payment terms, IBAN/BIC/account, order ids beyond one, tax-breakdown grain, line SKU as a separate code field). The XML has many elements the engine has no field for.
- The XSDs declare no signature element. The signature is a separate enveloped XMLDSig element required by the format spec.

## Sources (all read 2026-10-22)

Documentation page, the index of everything below:
https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Paginas/documentacionSobreE-CF.aspx

Path prefix `P` for the files: `https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/`

| Item | Version and date as listed on the page | File under `P` |
|---|---|---|
| XSD e-CF 31 | v.1.0, 16/10/2025 | `Documentaci%C3%B3n%20T%C3%A9cnica%20(XSD)/e-CF%2031%20v.1.0.xsd` |
| XSD e-CF 32 | v.1.0, 16/10/2025 | same folder, `e-CF%2032%20v.1.0.xsd` |
| XSD e-CF 41 | v.1.0, 16/10/2025 | same folder, `e-CF%2041%20v.1.0.xsd` |
| XSD e-CF 43 | v.1.0, 16/10/2025 | same folder, `e-CF%2043%20v.1.0.xsd` |
| XSD RFCE 32 (consumer summary) | v.1.0, 20/06/2023 | same folder, `RFCE%2032%20v.1.0.xsd` |
| Formato e-CF | V1.0, 30/10/2025 (document says "Octubre 2025", last bitacora entry 09-10-2025) | `Formatos%20XML/Formato%20Comprobante%20Fiscal%20Electr%C3%B3nico%20(e-CF)%20V1.0.pdf` |
| Representacion Impresa (modelos ilustrativos) | 22/04/2025 | `Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Representaci%C3%B3n%20Impresa%20(Modelos%20ilustrativos).pdf` |
| Descripcion Tecnica Emisores Electronicos | 29/05/2026 | `Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Descripcion%20Tecnica%20Emisores%20Electronicos.pdf` |
| Firmado de e-CF | 22/11/2023 | `Instructivos%20sobre%20Facturaci%C3%B3n%20Electr%C3%B3nica/Firmado%20de%20e-CF.pdf` |
| Proceso de Certificacion para ser Emisor Electronico | 19/08/2025 | `Documentaciones%20Proceso%20de%20Certificaci%C3%B3n%20FE/Proceso%20de%20Certificacion%20para%20ser%20Emisor%20Electronico.pdf` |

The XSD files returned HTTP 403 to a plain `curl` and 200 with a browser User-Agent. Downloaded to a scratch directory under /tmp/claude-1000 and not committed. Sizes and SHA-256 of what was read:

- E31: 123,019 bytes, SHA-256 `6f2909a93d84919518d2ae3c77fead4b35c3e8c95996b8af67b0040c2e2be298`
- E32: 122,998 bytes, `151676df03c61335b4ac1eca97f99395488d57ea92793b49353602871e651f3c`
- E41: 111,171 bytes, `eae1993c637375bc1cbe80932411d87f5680cd54e77e4d1b2752d72a6c8b2ab3`
- E43: 96,111 bytes, `776f030980c2c50cf0221e9263c55f367c8631727adff00ab45e2c7c1abafc52`

(Byte sizes differ from the page's KB figures, which are rounded; the page lists 120.1, 120.1, 108.6 and 93.9 KB. Not reconciled: unverified whether the page's KB is KiB and the files are unchanged.)

Types, from the format spec ("Tipos de e-CF" table, p. 2 of 87): 31 Factura de Credito Fiscal Electronica, 32 Factura de Consumo Electronica, 41 Compras Electronico, 43 Gastos Menores Electronico (plus 33, 34, 44, 45, 46, 47).

## What the XSDs say

All four: no namespace, root `ECF`, `versionType` enumerates only `1.0`, `TipoeCFType` enumerates the ten type codes. e-NCF pattern is 13 alphanumerics. RNC pattern is 9 or 11 digits. Dates are `dd-MM-yyyy`. The XSD itself contains no `Signature` element (zero matches); the signature is described in the format spec (section H, "Firma Digital", `<Signature>`, "Firma digital sobre todo el documento", p. 58 of 87).

Top-level structure, identical in 31 and 32; 41 and 43 are subsets:

| Section | E31 | E32 | E41 | E43 |
|---|---|---|---|---|
| `Encabezado/IdDoc` | yes | yes (no `FechaVencimientoSecuencia`) | yes | yes (only TipoeCF, eNCF, FechaVencimientoSecuencia, TipoPago, TotalPaginas) |
| `Encabezado/Emisor` | yes | yes | yes | yes |
| `Encabezado/Comprador` | RNC and razon social required | RNC, razon social, `IdentificadorExtranjero` optional | RNC and razon social required | absent |
| `Encabezado/InformacionesAdicionales`, `Transporte` | yes | yes | no | no |
| `Encabezado/Totales` | full ITBIS, additional taxes, retention/perception totals | same minus the four retention/perception totals | gravado, ITBIS, retention totals, MontoTotal | only MontoExento, MontoTotal, period/balance fields |
| `Encabezado/OtraMoneda` | yes | yes | currency fields live inside Totales (`TipoMoneda`, `TipoCambio`) | yes, exempt and total only |
| `DetallesItems/Item` (1 to 1000) | yes, with `Retencion` optional | yes, `Retencion` absent, `Mineria` added | yes, `Retencion` required | reduced: no discount, surcharge or tax fields |
| `Subtotales`, `DescuentosORecargos`, `Paginacion`, `InformacionReferencia` | yes | yes | yes (page fields flattened into DescuentosORecargos) | `Subtotales`, `Paginacion`, `InformacionReferencia` only |
| `FechaHoraFirma` | required | required | required | required |

Source for the table: the four XSD files above, element trees extracted 2026-10-22. Maximum 1,000 items per e-CF in all four (`Item maxOccurs=1000`).

Line item elements (E31): `NumeroLinea`, `TablaCodigosItem/CodigosItem(TipoCodigo, CodigoItem)` up to 5, `IndicadorFacturacion` (0 to 4), `Retencion`, `NombreItem` (max 80), `IndicadorBienoServicio` (1 or 2), `DescripcionItem` (max 1000), `CantidadItem`, `UnidadMedida` (62-value code list), `CantidadReferencia`, `PrecioUnitarioReferencia`, `FechaElaboracion`, `FechaVencimientoItem`, `PrecioUnitarioItem`, `DescuentoMonto` plus `TablaSubDescuento`, `RecargoMonto` plus `TablaSubRecargo`, `TablaImpuestoAdicional`, `OtraMonedaDetalle`, `MontoItem`.

`IndicadorFacturacion` encodes the ITBIS rate class, not the rate: 0 not billable, 1 ITBIS1 (18%), 2 ITBIS2 (16%), 3 ITBIS3 (0%), 4 exempt (format spec, Detalle de Bienes o Servicios, field "Indicador de Facturacion"). Rates themselves are in the header as `ITBIS1`, `ITBIS2`, `ITBIS3` (integers). Per ticket rules, fiscal rates here are quoted from the spec text only to describe the encoding and must be re-fetched at the moment of use.

## Representacion impresa

The Representacion Impresa models PDF (22/04/2025) is image-only: its text layer holds the table of contents and footnotes, and the illustrative layouts are images. I did not view the images. What the text confirms: models exist for E31 in several total/ITBIS layouts, debit and credit notes, a two-page e-CF with pagination, consumer invoices at or above and below DOP 250,000, deferred-send and contingency forms. Footnote 1 of model 1.1: "el monto del campo 'Valor' del detalle de bienes o servicios, no incluye impuestos (precio x cantidad)". There is **no** model for E41 or E43 in the table of contents (unverified for the images). What the printed page must show beyond the XML (QR code, security code, signature date) is **unverified**: not extracted from the images.

## Signature envelope

From "Firmado de e-CF" (22/11/2023) and the technical description (29/05/2026):

- Enveloped XMLDSig, `<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">` appended in the `ECF` root, `<Reference URI="">`, enveloped-signature transform, RSA-SHA256 signature method, SHA-256 digest, certificate in `X509Data`.
- Signed without whitespace preservation (`PreserveWhitespace = false`), and "once signed the XML cannot be altered under any circumstance". The certificate's `SN` must equal the owner's RNC, cedula or passport (technical description, "Firmado de XML").
- The signed content covers header, detail, discounts and surcharges, pagination, reference information and `FechaHoraFirma` (format spec section H).

File name convention (technical description): e-CF is `RNCEmisor + e-NCF` + `.xml`, example `101672919E310000000001.xml`; consumer summary (E32 under 250,000) `RNCEmisor + e-NCF`.

## Field correspondence with the engine's invoice schema

Engine schema: `engine/src/docmatch/extraction/schema.py` at `origin/main` (46e448c), 36 header fieldtypes (lists) and 19 line cells. The XML paths are for E31; differences for the other types are in the last column.

Header

| Engine field | XML element | Types where it differs |
|---|---|---|
| `document_id` | `Encabezado/IdDoc/eNCF` (also `Emisor/NumeroFacturaInterna` is the issuer's internal number) | none |
| `order_id` | `Comprador/NumeroOrdenCompra` | E32: present; E41, E43: absent |
| `customer_order_id` | `Comprador/NumeroOrdenCompra` | same |
| `vendor_order_id` | `Emisor/NumeroPedidoInterno` | present in all four |
| `payment_reference` | none | |
| `date_issue` | `Emisor/FechaEmision` | all four |
| `date_due` | `IdDoc/FechaLimitePago` | absent in E43 |
| `payment_terms` | `IdDoc/TerminoPago` (free text) plus `TipoPago` code 1 to 3 | E43: `TipoPago` only |
| `vendor_name` | `Emisor/RazonSocialEmisor` (`NombreComercial` optional) | all |
| `vendor_address` | `Emisor/DireccionEmisor` (plus `Municipio`, `Provincia` codes) | all |
| `vendor_email` | `Emisor/CorreoEmisor` | all |
| `vendor_tax_id` | `Emisor/RNCEmisor` | all |
| `vendor_registration_id` | none | |
| `customer_id` | `Comprador/CodigoInternoComprador` (closest) | absent in E43 |
| `customer_billing_name` | `Comprador/RazonSocialComprador` | E32 optional; E43 absent |
| `customer_billing_address` | `Comprador/DireccionComprador` | E43 absent |
| `customer_delivery_name` | `Comprador/ContactoEntrega` (closest) | E31, E32 only |
| `customer_delivery_address` | `Comprador/DireccionEntrega` | E31, E32 only |
| `customer_other_name`, `customer_other_address` | none | |
| `customer_tax_id` | `Comprador/RNCComprador` (E32 also `IdentificadorExtranjero`) | E43 absent |
| `customer_registration_id` | none | |
| `amount_total_net` | `Totales/MontoGravadoTotal` plus `MontoExento` (no single net total element) | derived |
| `amount_total_tax` | `Totales/TotalITBIS` (plus `MontoImpuestoAdicional`) | E43: none |
| `amount_total_gross` | `Totales/MontoTotal` | all |
| `amount_due` | `Totales/ValorPagar` | all optional |
| `amount_paid` | `Totales/MontoAvancePago` (closest) | all optional |
| `currency_code_amount_due` | `OtraMoneda/TipoMoneda` only when not DOP; DOP is implicit | E41: `Totales/TipoMoneda` |
| `tax_detail_net` | `Totales/MontoGravadoI1`, `I2`, `I3` | E43 none |
| `tax_detail_tax` | `Totales/TotalITBIS1`, `2`, `3` | E43 none |
| `tax_detail_gross` | none (derive) | |
| `tax_detail_rate` | `Totales/ITBIS1`, `ITBIS2`, `ITBIS3` | E43 none |
| `iban`, `bic`, `account_num`, `bank_num` | `IdDoc/NumeroCuentaPago`, `BancoPago`, `TipoCuentaPago` map to `account_num` and bank only; no IBAN or BIC | E31, E32, E41 |

No engine field: `eNCF` type code and `FechaVencimientoSecuencia`, `TipoIngresos`, `FormaPago` table, additional taxes (ISC), retention and perception totals (`TotalITBISRetenido`, `TotalISRRetencion`, `TotalITBISPercepcion`, `TotalISRPercepcion`), `InformacionReferencia` (`NCFModificado`, `CodigoModificacion`), `OtraMoneda` amounts and `TipoCambio`, `FechaHoraFirma`, transport, subtotals, pagination, mining block (E32).

Line items

| Engine cell | XML element | Note |
|---|---|---|
| `line_item_position` | `NumeroLinea` | |
| `line_item_code` | `TablaCodigosItem/CodigosItem/CodigoItem` (with `TipoCodigo`) | up to 5 per line; engine holds one |
| `line_item_description` | `NombreItem` (80) and `DescripcionItem` (1000) | two elements for one cell |
| `line_item_quantity` | `CantidadItem` | |
| `line_item_units_of_measure` | `UnidadMedida` | numeric code, 62 values, not text |
| `line_item_unit_price_net` | `PrecioUnitarioItem` | assumed net: representation models say the line value excludes taxes; unverified for the unit price |
| `line_item_unit_price_gross` | none | |
| `line_item_amount_net` | `MontoItem` | same assumption |
| `line_item_amount_gross` | none | |
| `line_item_tax` | none per line; only `Retencion/MontoITBISRetenido` | tax is header-level |
| `line_item_tax_rate` | `IndicadorFacturacion` (class 1 to 4, rate in header) | |
| `line_item_discount_amount` | `DescuentoMonto` | not in E43 |
| `line_item_discount_rate` | `TablaSubDescuento/SubDescuento/SubDescuentoPorcentaje` | not in E43 |
| `line_item_currency` | none | |
| `line_item_date` | `FechaElaboracion`, `FechaVencimientoItem` | not a service date |
| `line_item_order_id`, `line_item_person_name`, `line_item_hts_number` | none | |
| `line_item_weight` | none per line (`Subcantidad`, header `PesoBruto`) | |

Line elements with no engine cell: `IndicadorBienoServicio`, `CantidadReferencia`, `UnidadReferencia`, `PrecioUnitarioReferencia`, `GradosAlcohol`, `RecargoMonto` and sub-surcharges, `TablaImpuestoAdicional`, `OtraMonedaDetalle`, `Retencion`.

Consequence for a fixture: the XML half would exercise net amounts and a rate class, never a gross amount, a per-line tax amount or a currency, which the engine's matching and gate use. Which of those the engine requires is a question for #213/#214, not settled here.

## Is there a public sample set?

- DGII's documentation page lists no XML samples (page content read 2026-10-22; the section list is XSD, formats, instructivos, certification, technical description).
- DGII help portal, "PRUEBAS XML", asked 14 Oct 2019, official answer points to the guides, no sample: https://ayuda.dgii.gov.do/conversations/discusiones/pruebas-xml/5f3c17c48cd858ce87a95283
- DGII help portal, "Formato XML del e-CF de ejemplo", asked 27 Jan 2023, official answer: email facturacionelectronica@dgii.gov.do, no sample, no XSD: https://ayuda.dgii.gov.do/conversations/discusiones/formato-xml-del-ecf-de-ejemplo/63d424fe2c015d03d4e7fc99
- Certification process (19/08/2025, step "Pruebas de Datos"): the taxpayer downloads a test dataset **as an Excel file** from the certification portal and builds the XMLs from it; a rejected e-CF restarts the set. This is behind enrollment and is a spec, not a public corpus. Source: the certification PDF in the table above, step 2.
- Whether the free invoicer (Facturador Gratuito, https://fg.dgii.gov.do/ecf/PortalFG/login) can export XML, and whether its output could stand as samples, is **unverified**; it needs an account.
- Third-party samples exist (software vendors, repos) but are not primary sources and were not used.

So the XML half of a fixture would be generated from the published XSD, validated against it, and signed per the signing guide, not downloaded. Sample count is then a design choice rather than a supply limit. Generating from the XSD is itself a decision for the map (it is synthetic data, like the existing generator).

## Unverified or not done

- Representacion impresa images were not viewed; E41 and E43 printed models and QR or security-code requirements unverified.
- Whether `PrecioUnitarioItem` is net or gross: the spec text was not read for that cell.
- Whether the page's KB sizes match the downloaded bytes (files may have been revised after the listed dates).
- The page listing shows "Informe Tecnico e-CF v1.0" dated 16/10/2026; not opened.
- Formato PDF URL on the documentation page differs in case (`V1.0` there, `v1.0` in a search result); both were seen, only `V1.0` was downloaded.
- Facturador Gratuito XML export, and any DGII pre-validator service: not covered here (separate ticket on the map).
