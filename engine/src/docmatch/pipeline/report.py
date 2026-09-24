"""What `docmatch pipeline --run` reads off one saved loop run: latency, cost
and the routing ladder, which `ladder` works out.

End to end is the upload accepted to the loop settling the document at
`approved` or `needs_review`, queue wait included (#152). Per status, each
transition leaving it splits in two at the moment the loop took the document
up: wait, since the previous transition committed, and work, from then to its
own commit (#157). A lease that lapsed shows as wait, since the transition
records only the claim that committed.

Cost per document is every vendor call summed, retries included, over every
case in the run, and per status the calls made there. Only extraction calls a
vendor, so the other statuses read $0, with no compute rate made up (#152).
Latency is p50 and p95 by nearest rank, the extraction run's own
`percentile_of`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from docmatch.metrics.score import percentile_of
from docmatch.pipeline.ladder import Rung, ladder
from docmatch.pipeline.loop import PENDING, SETTLED, Status
from docmatch.pipeline.saved import LoopRun, LoopRunError, SavedCase


@dataclass(frozen=True)
class Spread:
    """Seconds at p50 and p95."""

    p50: float
    p95: float


def spread(values: Sequence[float]) -> Spread | None:
    """p50 and p95 of the values, or None when there are none."""
    if not values:
        return None
    return Spread(percentile_of(values, 50), percentile_of(values, 95))


@dataclass(frozen=True)
class StatusRow:
    """One status: how long documents waited there and were worked on, and
    what it cost per document."""

    status: Status
    wait: Spread | None
    work: Spread | None
    """None when no document left the status."""
    cost_per_document: Decimal


@dataclass(frozen=True)
class Report:
    run: LoopRun
    end_to_end_seconds: tuple[float, ...]
    """Seconds per case, in the run's order."""
    cost_per_document: Decimal
    statuses: tuple[StatusRow, ...]
    ladder: tuple[Rung, ...]

    @property
    def end_to_end_spread(self) -> Spread:
        found = spread(self.end_to_end_seconds)
        assert found is not None, "a loop run has at least one case"
        return found

    def status(self, status: Status) -> StatusRow:
        return next(each for each in self.statuses if each.status == status)


def report(run: LoopRun) -> Report:
    """The latency, cost and routing ladder of one loop run."""
    if not run.cases:
        raise LoopRunError("the loop run has no case, so there is nothing to report")
    counted = len(run.cases)
    waits: dict[Status, list[float]] = {each: [] for each in PENDING}
    works: dict[Status, list[float]] = {each: [] for each in PENDING}
    costs = dict.fromkeys(PENDING, Decimal(0))
    for case in run.cases:
        previous = None
        for transition in case.transitions:
            left = transition.from_status
            if (
                left in PENDING
                and transition.taken_at is not None
                and previous is not None
            ):
                waits[left].append((transition.taken_at - previous).total_seconds())
                works[left].append(
                    (transition.committed_at - transition.taken_at).total_seconds()
                )
            previous = transition.committed_at
        for each in case.vendor_calls:
            costs[each.status] += each.cost
    return Report(
        run=run,
        end_to_end_seconds=tuple(_end_to_end(case) for case in run.cases),
        cost_per_document=sum(costs.values(), Decimal(0)) / counted,
        statuses=tuple(
            StatusRow(
                each, spread(waits[each]), spread(works[each]), costs[each] / counted
            )
            for each in PENDING
        ),
        ladder=ladder(run),
    )


def _end_to_end(case: SavedCase) -> float:
    """Seconds from the upload to the system settling the case, the span
    `loop.latency` reads from Postgres."""
    uploaded = next(
        each for each in case.transitions if each.from_status is None
    ).committed_at
    settled = next(
        (
            each
            for each in case.transitions
            if each.actor == "system" and each.to in SETTLED
        ),
        None,
    )
    if settled is None:
        raise LoopRunError(
            f"{case.document_id} never settled at approved or needs_review, so the "
            "loop run is not a finished one"
        )
    return (settled.committed_at - uploaded).total_seconds()
