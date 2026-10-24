"""The fiscal rules the 606 applies, each a table of values with the date it
takes effect and the source it was read from (#230, #208, #215).

Every value below was fetched from DGII's own pages on 2026-10-23 for this
module, never taken from the README or from memory. A value DGII does not
state is absent, not guessed.

ISR retention, 606 column 17 (`Tipo de Retención en ISR`)
---------------------------------------------------------

The codes are the instructivo's, "Instructivo Formato 606" (Llenado y Remisión
del Formato de Envío de Compras de Bienes y Servicios), DGII, PDF last
modified 2026-02-12, p. 7, read 2026-10-23
(https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/4-LlenadoyEnvioFormato606.pdf):
1 Alquileres, 2 Honorarios por servicios, 3 to 9 others. Column 18 is "el
resultado de multiplicar el monto del campo 'Servicios' por el porcentaje de
la retención según corresponda" (p. 7), so the base is the services amount.

Rates, types 1 and 2: DGII's help answers say Ley 30-26 raised them from 10
to 15 percent "a partir del 1 de julio de 2026":

- Alquileres: "Con la entrada en vigencia de la ley 30-26, el porcentaje de
  retención se aumenta (de 10% a 15%) en la prestación de servicios por
  concepto de alquiler o arrendamiento ... Este nuevo porcentaje es aplicable
  a partir del 1 de julio de 2026", CA3135, ayuda.dgii.gov.do, read
  2026-10-23
  (https://ayuda.dgii.gov.do/conversations/retenciones/ca3135-qu-retencin-debo-aplicar-en-la-prestacin-del-servicio-de-alquiler-o-arrendamiento-de-bienes-muebles-o-inmuebles/5f3c175f8cd858ce879a0be1).
- Honorarios: "el porcentaje de retención se aumenta de 10% a 15% en la
  prestación de servicios por concepto de honorarios", discussion "cambio en
  las retenciones", published 2026-07-14, read 2026-10-23
  (https://ayuda.dgii.gov.do/conversations/discusiones/cambio-en-las-retenciones/6a56a95f0748377e13945e1b).
  It gives no date; the 1 July 2026 date is CA3135's and the discussion
  "retenciones segun ley 30-26" (read 2026-10-23), which says the new rates
  "se encuentran vigentes desde el 1 de julio de 2026", Aviso 10-26
  (https://ayuda.dgii.gov.do/conversations/discusiones/retenciones-segun-ley-3026/6a42be2e3365d30845e40234).

The 10 percent before is the "de 10%" of those answers. Norma General
07-2007, Art. 3 ("Modificado por efecto de la Ley núm. 30-26"), read
2026-10-23 in DGII's published PDF, states 15 percent for professionals
invoicing only the service
(https://dgii.gov.do/legislacion/normasGenerales/Documents/NG%20sobre%20(ITBIS),%20NG%20sobre%20(ISR),%20NG%20sobre%20Comprobantes%20Fiscales/norma07-07.pdf).
Ley 30-26's own text was not read (unverified).

The date that picks the rate is the payment date when there is one:
"la fecha que deberá tomar en cuenta para determinar el porcentaje de
retención a aplicar es la fecha del pago", discussion "Retenciones 15%
honorarios profesionales", question dated 2026-07-20, read 2026-10-23
(https://ayuda.dgii.gov.do/conversations/discusiones/retenciones-15-honorarios-profesionales/6a5e1cdbf8ac4db6a2ca51f7).
This departs from #215 and #230, which said the comprobante date. A filing
annotation with no payment date falls back to the comprobante date (owner
decision on #230, ticket #249).

Types 3 to 9 have no rate in any source read, so none is given (unverified);
the export fails the document rather than guess. DGII's help answers show 10
percent of the total for "otras rentas" but the instructivo does not state
it and the answer is not a norm. The 2 percent technical-services rate
(Reglamento 139-98, Art. 70) has no 606 code of its own and is not modeled.

Consumo, 606 not filed under a threshold
----------------------------------------

"Los NCF de consumo y/o B02 no se reportan a través del Formato 606 cuando su
monto no exceden la suma de RD$250,000.00 pesos", a DGII help answer
(discussion "formulario 606 ncf b02", question dated 2019-09-30, read
2026-10-23; the page cites no norma and gives no effective date, so the
rule is dated `None`, unverified as law)
(https://ayuda.dgii.gov.do/conversations/discusiones/formulario-606-ncf-b02/5f3c17bc8cd858ce87a713bc).
"No exceden" is at or below. DGII does not define "monto" (unverified):
the code reads it as the net amount plus ITBIS, selective and other taxes and
the tip. The consumo types are B02 and E32. The
pre-validator itself rejects a B02 (and an E32) at any amount, under or
over the threshold (#215, 2026-10-23), so it does not encode the threshold.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

CONSUMO_THRESHOLD = Decimal("250000.00")
"""A consumo comprobante of this amount or less is not reported."""

CONSUMO_TYPES = ("B02", "E32")


def is_consumo(ncf: str) -> bool:
    """Whether a compacted NCF or e-NCF is a consumo: B02 or E32."""
    return ncf[:3] in CONSUMO_TYPES


@dataclass(frozen=True)
class Rate:
    """A rate and the first day it applies on; None is from the beginning of
    what DGII states."""

    since: date | None
    rate: Decimal


ISR_RETENTION_RATES: dict[int, tuple[Rate, ...]] = {
    retention_type: (
        # Unverified in a norm: rests on the help answers' "de 10%" wording
        # (see the docstring); Norma 07-2007 Art. 3 does not state it.
        Rate(None, Decimal("0.10")),
        Rate(date(2026, 7, 1), Decimal("0.15")),
    )
    for retention_type in (1, 2)
}
"""Column 17's code to its rates, oldest first."""


def isr_retention_rate(retention_type: int, on: date) -> Decimal | None:
    """The rate in force on `on` for a retention type, or None when DGII
    states none for it."""
    in_force = [
        each.rate
        for each in ISR_RETENTION_RATES.get(retention_type, ())
        if each.since is None or each.since <= on
    ]
    return in_force[-1] if in_force else None
