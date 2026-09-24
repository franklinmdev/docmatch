"""The routing ladder over one saved loop run: what each policy costs in review
against what it lets escape.

Every rung reruns `routing.route` over each case's saved standing, so a
routing change is measured without paying for extraction again (#152). P4 is
the policy the loop ran, so its recomputation must give back, case by case,
the status and reasons the loop wrote; a run where it does not is refused
rather than reported. P5 is there only when the backend reported confidence.

Review rate is over every case in the run. An escaped document is one a rung
approves that should have gone to review, for one of two causes: an injected
discrepancy of a type that holds, or a gate fieldtype read with a value its
label does not have. A missing line is a note, so a case whose only injected
discrepancy is one never escapes. Escape rate and each cause's rate are over
the documents the rung approves; a document escaping on both causes counts
once in the escape rate and once under each cause, so the causes can sum
above it (#167).
"""

from dataclasses import dataclass

from docmatch import gate
from docmatch.matching.matcher import SEVERITY
from docmatch.metrics.fields import FieldValues, score_fields
from docmatch.pipeline.routing import LADDER, P4, P5, Policy, route
from docmatch.pipeline.saved import LoopRun, LoopRunError, SavedCase


@dataclass(frozen=True)
class Rung:
    """One policy over one run: documents sent to review, and those approved
    that escaped, by cause."""

    policy: Policy
    documents: int
    reviewed: int
    escaped: int
    injected_hold: int
    misread: int

    @property
    def approved(self) -> int:
        return self.documents - self.reviewed

    @property
    def review_rate(self) -> float:
        return self.reviewed / self.documents

    @property
    def escape_rate(self) -> float | None:
        """None when the rung approves nothing."""
        return self._over_approved(self.escaped)

    @property
    def injected_hold_rate(self) -> float | None:
        return self._over_approved(self.injected_hold)

    @property
    def misread_rate(self) -> float | None:
        return self._over_approved(self.misread)

    def _over_approved(self, count: int) -> float | None:
        return count / self.approved if self.approved else None


def reports_confidence(run: LoopRun) -> bool:
    """Whether the backend returned a confidence for any case it read."""
    return any(each.confidence is not None for each in run.cases)


def ladder(run: LoopRun) -> tuple[Rung, ...]:
    """Every rung from P0 to P4, then P5 at each edge on a run with confidence."""
    _check_p4(run)
    policies = LADDER + (P5 if reports_confidence(run) else ())
    return tuple(_rung(run.cases, each) for each in policies)


def _rung(cases: tuple[SavedCase, ...], policy: Policy) -> Rung:
    approved = [each for each in cases if not route(each.standing, policy)]
    injected = [_injected_hold(each) for each in approved]
    misread = [bool(each.misread) for each in approved]
    return Rung(
        policy=policy,
        documents=len(cases),
        reviewed=len(cases) - len(approved),
        escaped=sum(one or other for one, other in zip(injected, misread, strict=True)),
        injected_hold=sum(injected),
        misread=sum(misread),
    )


def _injected_hold(case: SavedCase) -> bool:
    return any(SEVERITY[each.type] == "hold" for each in case.truth)


def _check_p4(run: LoopRun) -> None:
    """Refuse a run whose saved routing P4 does not give back."""
    for case in run.cases:
        reasons = route(case.standing, P4)
        status = "needs_review" if reasons else "approved"
        if (case.status, case.routing_reasons) != (status, reasons):
            raise LoopRunError(
                f"{case.document_id}: the loop reached {case.status} with "
                f"{list(case.routing_reasons)}, but P4 over its saved outputs "
                f"gives {status} with {list(reasons)}"
            )


def misread(header: FieldValues, labeled: FieldValues) -> tuple[str, ...]:
    """The gate's fieldtypes whose reading holds a value its label does not,
    compared the way field F1 compares them. A value left unread is none."""
    spurious = {
        each.fieldtype: each.spurious
        for each in score_fields(labeled, header).per_fieldtype
    }
    return tuple(each for each in gate.FIELDTYPES if spurious.get(each))
