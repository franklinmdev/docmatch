"""Routing: the reasons a document goes to review under one routing policy.

One pure function decides it from what is known of the document where it
stands. The loop runs P4 at `matched`, and for a document whose lease lapsed a
third time; the pipeline report reruns every policy of the routing ladder over
a saved loop run, with no model call (#152).

The ladder is cumulative, each policy routing everything the one before does
and more:

- P0 routes nothing.
- P1 adds the two shortcuts, extraction failed and pipeline failed, which route
  where the document stands (#155).
- P2 adds gate failed. A gate that checked nothing never routes.
- P3 adds match held on price variance or tax mismatch.
- P4 adds match held on every hold type: the loop's own policy (#150). A match
  whose only findings are notes never routes.
- P5 adds the lowest confidence among the values the gate's checked rules
  used falling below an edge, printed at every fixed edge from 0.1 to 0.9 and
  never one edge chosen (#77), on a backend that reports confidence only. A
  value with no confidence is never below one, the sweep's own rule (#23).
  Confidence only ever adds review (rule 4).

Reasons come in the order a reviewer reads them, which is the order the loop
writes them: extraction failed, gate failed, match held, confidence, and last
pipeline failed, after every reason known where the document stood.
"""

from dataclasses import dataclass, replace
from typing import Literal, cast, get_args

from docmatch.evals.confidence import EDGES, below_edge
from docmatch.gate import GateResult, Verdict
from docmatch.matching.matcher import SEVERITY, TYPES, DiscrepancyType, MatchResult

Shortcut = Literal[
    "extraction failed",
    "pipeline failed at received",
    "pipeline failed at extracted",
    "pipeline failed at validated",
    "pipeline failed at resolved",
    "pipeline failed at matched",
]
"""The reasons that route a document where it stands, before `matched`.
Pipeline failed names the status the loop kept failing to move the document
past, one reason per pending status."""

RoutingReason = Literal["gate failed", "match held"] | Shortcut
"""Why the loop sends a document to review: the glossary's four reasons."""

LowConfidence = Literal["confidence below the edge"]
"""P5's reason, a rung of the ladder and never one the loop gives."""

CONFIDENCE: LowConfidence = "confidence below the edge"

Reason = RoutingReason | LowConfidence
"""Why a policy sends a document to review."""


@dataclass(frozen=True)
class Standing:
    """What is known of a document where routing decides it."""

    shortcut: Shortcut | None = None
    gate: Verdict | None = None
    """None when the reading never reached the gate."""
    holds: tuple[DiscrepancyType, ...] = ()
    """The types of the hold findings matching gave, in report order."""
    gated_confidence: tuple[float | None, ...] = ()
    """The confidence of every value the gate's checked rules used."""


@dataclass(frozen=True)
class Policy:
    """One rung of the routing ladder: which reasons send a document to review."""

    name: str
    adds: str
    """What this rung routes beyond the one before, as the report prints it."""
    shortcuts: bool = False
    gate: bool = False
    holds: frozenset[DiscrepancyType] = frozenset()
    edge: float | None = None


P0 = Policy("P0", "nothing")
P1 = replace(P0, name="P1", adds="extraction or pipeline failed", shortcuts=True)
P2 = replace(P1, name="P2", adds="gate failed", gate=True)
P3 = replace(
    P2,
    name="P3",
    adds="match held on price variance or tax mismatch",
    holds=frozenset({"price variance", "tax mismatch"}),
)
P4 = replace(
    P3,
    name="P4",
    adds="match held on any hold type, the loop's policy",
    holds=frozenset(each for each in TYPES if SEVERITY[each] == "hold"),
)
LADDER: tuple[Policy, ...] = (P0, P1, P2, P3, P4)
"""Every rung but P5, which only a backend reporting confidence has."""


def p5(edge: float) -> Policy:
    """P5 at one edge: P4 plus a gated value's confidence below it."""
    return replace(P4, name="P5", adds=f"confidence below {edge:.1f}", edge=edge)


P5_AT_EACH_EDGE: tuple[Policy, ...] = tuple(p5(edge) for edge in EDGES)


def route(known: Standing, policy: Policy) -> tuple[Reason, ...]:
    """Every reason the policy finds in what is known, in the order a reviewer
    reads them; none means the system approves the document."""
    reasons: list[Reason] = []
    if policy.shortcuts and known.shortcut == "extraction failed":
        reasons.append("extraction failed")
    if policy.gate and known.gate == "failed":
        reasons.append("gate failed")
    if any(each in policy.holds for each in known.holds):
        reasons.append("match held")
    if policy.edge is not None and below_edge(known.gated_confidence, policy.edge):
        reasons.append(CONFIDENCE)
    if (
        policy.shortcuts
        and known.shortcut is not None
        and known.shortcut != "extraction failed"
    ):
        reasons.append(known.shortcut)
    return tuple(reasons)


def standing(
    checked: GateResult | None,
    result: MatchResult | None,
    *,
    shortcut: Shortcut | None = None,
    gated_confidence: tuple[float | None, ...] = (),
) -> Standing:
    """What the saved gate and match results say; an output not yet saved says
    nothing."""
    return Standing(
        shortcut=shortcut,
        gate=None if checked is None else checked.verdict,
        holds=()
        if result is None
        else tuple(
            each
            for each in TYPES
            if any(
                finding.type == each and finding.severity == "hold"
                for finding in result.findings
            )
        ),
        gated_confidence=gated_confidence,
    )


def shortcut_of(reasons: tuple[str, ...]) -> Shortcut | None:
    """The shortcut among a document's routing reasons, if it took one."""
    found = [each for each in reasons if each in get_args(Shortcut)]
    return cast(Shortcut, found[0]) if found else None
