# Dominican fiscal rules: ITBIS, ISR retention, ITBIS retention, legal tip

Research for ticket #208 (part of map #205). Read on 2026-10-22. Branch only, never merged.

Method: primary sources only (the Codigo Tributario text and the normas generales as published by DGII, the Codigo de Trabajo as published by the Ministerio de Trabajo, DGII's e-CF technical format and 606 instructivo). Where the only DGII statement found is a Comunidad de Ayuda answer, it is labelled "DGII help answer", which is the agency's own voice but not the legal text. The README's figures were not used as a source. Anything not pinned is marked "unverified".

## Summary (what a RulesAdapter can assert, and how sure)

| Rule | Value today | Basis | Confidence |
|---|---|---|---|
| ITBIS general rate | 18% | DGII ITBIS page; Norma 06-23 caps ITBIS at 18% of billed; e-CF format 18% | High on the rate, but see "Rate conflict" below |
| ITBIS reduced rate | 16%, only for a closed list of goods (yogurt, butter, coffee, edible oils/fats, sugar, cocoa/chocolate) | Codigo Tributario Art. 345 Parr. II (Ley 253-12) | High |
| ITBIS zero rate | 0% on exports | Art. 342; e-CF format ITBIS3 = 0% | High |
| ITBIS exempt | closed lists of goods (Art. 343) and 9 services (Art. 344) | Ley 253-12 | High |
| ISR retention, rent and fees paid to an individual | 15% since 2026-07-01 (was 10%) | Ley 30-26 as stated by DGII help answers and DGII's amended Norma 07-2007 text | Medium: law text itself not read |
| ISR retention, technical services by an individual | 3% effective since July 2026 (was 2%) | Same | Low: DGII's own staff say it is 15% on a presumed 20% net income |
| ITBIS retention, company to individual (taxable service) | 100% of ITBIS | DGII Retenciones guide, Jul 2025 | High |
| ITBIS retention, company to company, liberal professions / movable-property rent | 30% of ITBIS, but not when payee is an authorised e-CF issuer billing by e-CF (Norma 02-2026, 2026-09-16) | Norma 02-05 art. 5 (as amended by 07-09); Norma 02-2026 | High |
| ITBIS retention, security/surveillance, non-profit payee, informal-supplier comprobante | 100% | Norma 07-09; Norma 01-11; Norma 05-19 (via DGII guide) | Medium: guide text only, norms not opened |
| Legal tip | 10% of the bill, dine-in only, outside the ITBIS base | Codigo de Trabajo Art. 228; Art. 339 Cod. Trib. | High |

## 1. ITBIS rates

### General rate: 18%, with a conflict in the statute text

- DGII's ITBIS page says: "En cumplimiento con lo establecido en los parrafos I y II del art. 23 de la Ley 253-12, se informa a los contribuyentes que la tasa del ITBIS a aplicar a las transferencia de bienes gravadas y/o prestacion de servicios a partir del 2016 sera del 18%." https://dgii.gov.do/cicloContribuyente/obligacionesTributarias/principalesImpuestos/Paginas/Itbis.aspx (read 2026-10-22).
- DGII's consolidated Titulo III text of the Codigo Tributario (Ley 11-92) shows Art. 345 as modified by Ley 253-12 art. 23: "i) 18% para el ano 2013; ii) 18% para el ano 2014; iii) 16% a partir del ano 2015", with Parrafo I saying the reduction applies only "en la medida que permita alcanzar y mantener la meta de presion tributaria al ano 2015". Art. 341 still prints 16% with a DGII footnote pointing to Art. 345. https://dgii.gov.do/legislacion/codigotributario/cdigo%20tributario/titulo3.pdf (read 2026-10-22).
- Rate conflict: the statute text literally says 16% from 2015, conditional; DGII applies 18%. A RulesAdapter must follow DGII's applied rate (18%), not the printed article. Corroborating DGII documents that treat 18% as current: Norma General 06-2023 (ITBIS paid "Debe ser igual o menor al 18% del Monto Facturado") https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20Impuesto%20sobre%20Transferencias%20de%20Bienes%20Industrializados%20y%20Servicios%20(ITBIS)/Norma%2006-23.pdf (read 2026-10-22); e-CF format v1.0 (ITBIS Tasa 1 = 18%) https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Formatos%20XML/Formato%20Comprobante%20Fiscal%20Electr%C3%B3nico%20(e-CF)%20v1.0.pdf (read 2026-10-22; PDF modified 2025-10-29).

### Reduced rate: 16% on listed goods

Art. 345 Parr. II (Ley 253-12) sets a reduced rate that stepped 8% (2013), 11% (2014), 13% (2015), 16% (from 2016) for HS-coded goods: yogurts (0403.10, 0403.90), butter (0405.10), coffee (09.01 with exceptions), edible oils and fats (15.07, 15.08, 15.11, 15.12, 15.13, 1515.21, 1515.29, 1517.10), sugars (1701.11, 1701.12, 1701.99), cocoa and chocolate (1805, 1806.10, 1806.32). Same source as above (titulo3.pdf, read 2026-10-22). The e-CF format carries it as ITBIS Tasa 2 = 16%. Whether the list was later changed is unverified (the consolidated text read is the DGII copy; no newer amending law was read).

### Zero rate

Art. 342: exported goods are "gravados con tasa cero". e-CF: ITBIS Tasa 3 = 0%. Norma General 07-2023 also records that port/airport cargo-handling services are zero-rated or exempt under Norma 15-07 and Reglamento 293-11 art. 14 k) 5. https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20(ITBIS),%20NG%20sobre%20(ISR),%20NG%20sobre%20Comprobantes%20Fiscales/norma07-23.pdf (read 2026-10-22; it also confirms land transport exempt under Art. 344 num. 3).

### Exempt

- Services, Art. 344 (Ley 253-12), verbatim list of nine: financial services including insurance; pension plans; land transport of persons and cargo; electricity, water and garbage collection; rental of dwellings; health; education and culture; funeral; beauty salons and barbershops. titulo3.pdf (read 2026-10-22). A service not on the list is taxed (DGII help answer, not fetched in full; unverified as a quote).
- Goods, Art. 343 (Ley 253-12): a long HS-coded list (live animals, basic foods, medicines, etc.). Not transcribed here; a RulesAdapter should treat the list as data to load from the article, not rules to hard-code. Same source.
- Special base: construction contracts that include materials bill 18% ITBIS on 10% of the total (Norma 07-2007 art. 4, as modified by Ley 253-12). https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20(ITBIS),%20NG%20sobre%20(ISR),%20NG%20sobre%20Comprobantes%20Fiscales/norma07-07.pdf (read 2026-10-22).

### On a comprobante and in the 606

- e-CF (format v1.0): per line an invoicing indicator picks the rate (1 = 18%, 2 = 16%, 3 = 0%, 4 = exempt); header carries MontoGravadoI1/I2/I3, MontoExento, ITBIS1/2/3 (rates), TotalITBIS1/2/3 and TotalITBIS. Validation: TotalITBIS1 = MontoGravadoI1 x ITBIS1. Source: e-CF format PDF above.
- 606: column 11 "ITBIS Facturado" is "el valor del ITBIS generado en el comprobante"; column 8 and 9 are services and goods amounts "sin incluir impuestos"; column 10 is their sum; column 15 "ITBIS por Adelantar" = column 11 minus column 14 (ITBIS llevado al costo), computed by the tool. https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/4-LlenadoyEnvioFormato606.pdf (DGII instructivo, PDF created 2026-02-12; read 2026-10-22). Column list cross-checked with recibox's research (below).

## 2. ISR retention (withholding by payer and payee kind)

### Who is a withholding agent

DGII's guide: per Art. 309 of the Codigo Tributario, withholding agents include public entities, personas juridicas and negocios de unico duenio, and civil associations/cooperatives. "Las Personas Fisicas, siempre y cuando no se trate de negocios de unico duenio, no se encuentran instituidas por el codigo tributario como agentes de retencion del ISR". https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/retencionesRetribucionesComplementarias/Documents/Retenciones-ISR-ITBIS.pdf (DGII guide dated "Julio 2025", "Publicacion informativa sin validez legal"; read 2026-10-22).

### Rates in the July 2025 guide (pre Ley 30-26)

Rent and leases paid to individuals 10%; fees and commissions paid to individuals 10%; services by individuals not in dependency 10% professional or 2% technical (examples: masonry, fumigation, cleaning, carpentry, painting, plumbing); State suppliers 5%; dividends 10%; interest to non-residents 5%; remittances abroad 27%; lottery prizes 25%; phone games 5%; interest paid by financial entities 1% (juridical and natural residents); beef subsector 1% (Norma 04-2025); transfer of title 2%; capital gain 1%. Same guide. Filing is on IR-17 by the 10th of the following month.

### Change in force: Ley 30-26 (effective 2026-07-01)

- A DGII help answer (Comunidad, dated by the poster 2026-07-06) says "Los cambios establecidos por el articulo 17 de la Ley 30-26 se encuentran vigentes a partir del 1 de julio de 2026", the ISR is "el 15%" computed "sobre la base de una renta neta presunta del 20% del valor bruto" for Art. 70 Reglamento 139-98 cases, and cites Ley 30-26 of 2026-06-18, Art. 309 Cod. Trib. https://ayuda.dgii.gov.do/conversations/discusiones/retenciones-de-isr-ley-3026-del-1862026/6a4c06530748377e13bf6c9f (read 2026-10-22).
- Another DGII answer: withholdings made from 2026-07-01 use the amended rule "aun cuando sean aplicadas a pagos por transacciones originadas en periodos anteriores". https://ayuda.dgii.gov.do/conversations/discusiones/retencin-isr-del-15-segn-ley-3026/6a452f52de3c6003da13b350 (read 2026-10-22).
- DGII's own published Norma 07-2007 now reads, Art. 3 "(Modificado por efecto de la Ley num. 30-26)": builders' masters 3%; engineers, architects and similar billing only the professional service 15% of the invoiced value. https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20(ITBIS),%20NG%20sobre%20(ISR),%20NG%20sobre%20Comprobantes%20Fiscales/norma07-07.pdf (read 2026-10-22).
- DGII's CA3135 help answer: rentals of movable and immovable property are withheld at 15% (up from 10%) from 2026-07-01 when payer is a legal person/sole owner and payee an individual; State to individual 5%; legal person to legal person none; non-profit payer none. https://ayuda.dgii.gov.do/conversations/retenciones-y-retribuciones-complementarias/ca3135-qu-retencin-debo-aplicar-en-la-prestacin-del-servicio-de-alquiler-o-arrendamiento-de-bienes-muebles-o-inmuebles/5f3c175f8cd858ce879a0be1 (read 2026-10-22).
- Not read: the text of Ley 30-26 itself (article 17 per DGII; a non-primary accounting blog says article 23, unverified). Treat 15% on rent and fees as DGII-stated but law text unverified. The July 2025 guide on DGII's site has not been updated and still shows 10%/2%, so DGII's published documents disagree with each other; a fixture or check must carry an effective date.
- Technical services by individuals: the 2% became "3%" on the IR-17 form (15% x presumed 20% net income = 3%) per DGII help answers; one answer told taxpayers who withheld 2% in July to "corregir la retencion o asumir la diferencia" (unverified wording, from a search summary). Treat as 3% effective, low confidence.

### Which 606 column carries it

- Column 17 "Tipo de Retencion en ISR" codes: 01 Alquileres, 02 Honorarios por servicios, 03 Otras rentas, 04 Otras rentas (rentas presuntas), 05 Intereses a personas juridicas residentes, 06 Intereses a personas fisicas residentes, 07 Retencion por proveedores del Estado, 08 Juegos telefonicos, 09 Retenciones subsector ganaderia de carne bovina.
- Column 18 "Monto Retencion Renta": "el resultado de multiplicar el monto del campo 'Servicios' por el porcentaje de la retencion segun corresponda". Both require column 7 (payment date). Instructivo above.
- Consequence for a check: the 606 base for ISR retention is the services amount (column 8), excluding ITBIS and, per DGII help answer, excluding tip ("La retencion no aplica por el monto percibido por concepto de propinas", Ley 11-92 Art. 307) https://ayuda.dgii.gov.do/conversations/discusiones/la-propina-legal-del-10-debe-calcularse-del-total-incluido-impuestos-o-del-subtotal-sin-impuestos/64ffa8cd503ed17eb5c4a345 (read 2026-10-22).
- On the e-CF: TotalISRRetencion (condicional, present only if a detail line carries a retention) = sum of the lines' Monto Retencion Renta; present for types 31, 33, 34, 41 (not consumo 32). e-CF format PDF.

## 3. ITBIS retention

### Norma General 02-05 (17 Jan 2005, modified by 13-07 and 07-09)

Text read: https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20Impuesto%20sobre%20Transferencias%20de%20Bienes%20Industrializados%20y%20Servicios%20(ITBIS)/norma02-05.pdf (read 2026-10-22).
- Art. 1 and 3: sociedades withhold when paying liberal professional services or movable-property rentals to other sociedades. Art. 2 lists liberal professions (engineering, architecture, accounting, auditing, law, IT, administration, design, advisory/consulting). Art. 4 lists rentals (equipment except telephone, machinery, motor vehicles, event furniture); if bundled with other services or goods, withhold on all the ITBIS billed.
- Art. 5: 30% of the ITBIS billed (Art. 1 and Art. 5 are modified by 07-09; the guide below attributes 100% to security and surveillance under 07-09).
- Art. 6 (as modified by 13-07): airlines, hotels and insurers paying commissions to travel agencies/brokers withhold the full ITBIS.
- Art. 7: the expense is ISR-deductible only if the retention is made and paid.

### Other cases, from DGII's guide (July 2025, not legal text)

Retenciones-ISR-ITBIS.pdf above:
- Company or sole owner paying a taxable service by an individual: 100% of ITBIS (IT-1 section A).
- Juridical persons paying advertising or other taxable services to a non-profit entity: 100% of the ITBIS billed (Norma 01-11).
- Security or surveillance between sociedades: 100% (Norma 07-09).
- Purchases from individuals not registered as taxpayers (comprobante de compras, NCF type 11): 100% of ITBIS in all cases (Norma 05-19); the buyer must check the RNC registry.
- Construction: individual 100%, legal entity 30% (Norma 07-2007 art. 3 Parr. IV and art. 4, confirmed in norma07-07.pdf above).
- Acquirers (card processors) withhold 2% of the billed amount on card transactions; 18% perceived if the merchant is not an active taxpayer and the operation exceeds RD$300,000 (Norma 06-2023 art. 4). Norma 06-23 text read (URL above).
- RST (simplified regime) matrix in the guide: ITBIS 100% when an ordinary-regime buyer pays an RST seller; ISR 10% when a juridical buyer pays an individual (matrix shows 10%, pre Ley 30-26; unverified whether updated).

### New exception: Norma General 02-2026 (signed 2026-09-16)

Art. unico: sociedades designated by 02-05 and its modifications "no efectuaran las retenciones previstas en dicha norma cuando realicen pagos a otra persona juridica ... siempre que dicha persona juridica se encuentre autorizada por la DGII como emisora electronica y la operacion sea facturada mediante un e-CF". Parrafo: only 02-05 retentions are excluded. https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20Impuesto%20sobre%20Transferencias%20de%20Bienes%20Industrializados%20y%20Servicios%20(ITBIS)/Norma02-26.pdf (read 2026-10-22). So since 2026-09-16 the 30% case does not apply for e-CF-issuing payees; a check must look at issuer status and comprobante type. It does not touch the 100% cases from individuals.

### On a comprobante and in the 606

- 606 column 12 "ITBIS Retenido": "el valor del ITBIS que se retuvo producto de la transaccion (si aplica). Siempre que se llene este campo, debe haber completado la casilla 7 (fecha pago)." Column 7 is the payment date. Instructivo above. Validation per recibox: column 12 at most column 11.
- e-CF: TotalITBISRetenido plus per-line Monto ITBIS Retenido (types 31, 33, 34, 41).

## 4. Legal tip (propina legal)

- Source of the 10%: Codigo de Trabajo (Ley 16-92) Art. 228: "En los hoteles, restaurantes, cafes, barras y en general, en los establecimientos comerciales donde se expende para su consumo en esos mismos lugares comidas o bebidas, es obligatorio para el empleador agregar un diez por ciento por concepto de propina en las notas o cuentas de los clientes ... a fin de ser distribuido integramente entre los trabajadores". https://mt.gob.do/wp-content/uploads/2024/07/codigo_de_trabajo.pdf (Ministerio de Trabajo PDF, read 2026-10-22; an older printing of the Code, so later amendments are unverified; a 2024 draft reform is on mt.gob.do and was not treated as law).
- Scope: dine-in only. The Poder Judicial published a note that the Supreme Court's Third Chamber held the 10% cannot be charged on take-away/delivery consumption. https://poderjudicial.gob.do/scj-establece-que-negocios-no-pueden-cobrar-10-de-propina-si-el-consumo-no-ocurrio-en-establecimientos/ (appeared in search results, page not opened; unverified detail).
- Not in the ITBIS base: Art. 339 num. 3 Cod. Trib.: for services the base is "El valor total de los servicios prestados, excluyendo la propina obligatoria". titulo3.pdf. DGII help answers confirm the base excludes the tip and cite Ley 11-92 Art. 339 and Decreto 293-11 Art. 9. https://ayuda.dgii.gov.do/conversations/discusiones/cul-es-la-base-imponible-de-la-propina-legal-del-10/65008cf1923c104940d162d5 (read 2026-10-22). The same DGII answers say the voluntary tip is also not taxed.
- Arithmetic implied: tip = 10% of the pre-tax service value; total = value + 18% ITBIS on value + tip. Whether the 10% is computed on the value before or after ITBIS is not stated in Art. 228 ("diez por ciento ... en las notas o cuentas"); the DGII help thread titled with that question answers about the ITBIS base only, so the tip's own base (pre-tax vs ITBIS included) is unverified.
- On the e-CF: additional-tax Table I code 001 "Propina Legal", rate 10%, summed into MontoImpuestoAdicional (codes 001 to 005 are the "otros impuestos adicionales"). e-CF format PDF, Tabla I. DGII's free e-CF invoicer does not support additional taxes or the legal tip (DGII FAQ result, not opened; unverified).
- In the 606: column 22 "Monto Propina Legal". The instructivo (created 2026-02-12) says the tip is the one "establecida por la Ley No. 54-32 (10%)". That citation is wrong or archaic against the Code of Labor Art. 228 and Norma 06-2023, which cites "Articulo 228 de la Ley 16-92 (Codigo de Trabajo)"; use Art. 228 as the legal source and note the instructivo's label. Norma 06-23 also excludes the tip from the acquirer's "Monto facturado" and adds a separate "Propina Legal y Propina Voluntaria" field (described as 10% of the billed amount, and may not exceed it).

## 5. 606 columns (cross-check)

recibox's research (franklinmdev/recibox docs/research/dgii-606-607-608-export-spec.md, dated 2026-06-04, `gh api` read 2026-10-22) lists the 606 as 23 pipe-delimited columns under Norma General 07-2018 (amended 05-2019): 1 RNC/Cedula, 2 Tipo Id, 3 Tipo de bienes y servicios (01 to 11), 4 NCF, 5 NCF modificado, 6 Fecha comprobante, 7 Fecha de pago, 8 Monto facturado en servicios, 9 en bienes, 10 total (auto), 11 ITBIS facturado, 12 ITBIS retenido, 13 ITBIS sujeto a proporcionalidad (Art. 349), 14 ITBIS llevado al costo, 15 ITBIS por adelantar (auto), 16 ITBIS percibido (disabled), 17 Tipo de retencion ISR, 18 Monto retencion renta, 19 ISR percibido (disabled), 20 Impuesto selectivo al consumo, 21 Otros impuestos/tasas, 22 Monto propina legal, 23 Forma de pago. The DGII instructivo read here agrees on the numbering and descriptions of columns 8 to 22 and on the 17 retention codes. Rules from recibox not re-verified here: 15th-of-month deadline, CRLF, header line.

Mapping used above:

| Fiscal item | 606 column(s) | Comprobante (e-CF) field |
|---|---|---|
| Goods/services base | 8, 9, 10 | MontoGravadoI1/I2/I3, MontoExento |
| ITBIS charged | 11 (and 15 derived) | TotalITBIS1/2/3, TotalITBIS |
| ITBIS retained | 12, needs 7 | TotalITBISRetenido |
| ISR retention type | 17, needs 7 | per-line retention indicator |
| ISR retained | 18, needs 7 | TotalISRRetencion |
| Legal tip | 22 | ImpuestoAdicional code 001, in MontoImpuestoAdicional |
| Selective consumption | 20 | ImpuestoAdicional codes 003 and up |

## Unverified or open

- The text of Ley 30-26 (rate changes, effective 2026-07-01); only DGII help answers and DGII's modified Norma 07-2007 art. 3 were read.
- The 3% technical-service retention (derived 15% x 20% presumed income) and whether 2% services-by-individual cases truly moved.
- The July 2025 DGII guide still shows 10%/2%; DGII publications disagree until the guide is refreshed.
- Whether a newer law re-fixed 18% in the statute text of Art. 345 (printed text says 16% from 2015).
- Exact text of Norma 05-19 and 01-11, 13-07, 07-09 (read only through the 02-05 consolidated text and DGII guide).
- The tip's calculation base (before vs after ITBIS), the take-away scope beyond the Poder Judicial headline, and later amendments to Art. 228.
- Art. 343 goods list not transcribed; reduced-rate list verified only as of Ley 253-12 text.
- Whether the e-CF format v1.0 PDF (modified 2025-10-29) is the latest; DGII may have newer XSDs.
