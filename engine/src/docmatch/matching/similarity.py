"""How alike two codes, or two descriptions, are: normalized Levenshtein.

One minus the edit distance over the longer length, so identical texts score
1.0, texts with nothing in common 0.0, and the measure is a true distance:
it is symmetric and a single edit always costs the same share of the longer
text. Hand-written, since it is thirty lines and the alternative in the
standard library, `difflib.SequenceMatcher.ratio`, is not a distance, scores
unrelated codes higher, and switches on an autojunk heuristic at 200
characters (#77).

The texts are compared as the scorer normalizes them, whitespace collapsed
and case dropped; that is the caller's job, so this function is one thing.
"""


def similarity(one: str, other: str) -> float:
    """1 minus edits over the longer length; two empty texts are alike."""
    longer = max(len(one), len(other))
    if longer == 0:
        return 1.0
    return 1 - _distance(one, other) / longer


def _distance(one: str, other: str) -> int:
    """The Levenshtein distance, two rows of the classic table at a time."""
    if one == other:
        return 0
    previous = list(range(len(other) + 1))
    for row, left in enumerate(one, start=1):
        current = [row]
        for column, right in enumerate(other, start=1):
            current.append(
                min(
                    previous[column] + 1,  # delete
                    current[column - 1] + 1,  # insert
                    previous[column - 1] + (left != right),  # substitute
                )
            )
        previous = current
    return previous[-1]


def at_least(one: str, other: str, floor: float) -> bool:
    """Whether `similarity(one, other) >= floor`, decided without the whole
    table: a pair whose lengths alone rule it out never starts, and the rows
    stop as soon as every cell is past the edits the floor allows. The
    out-of-catalog guard asks this of a singleton against every entry, where
    almost every pair is a long way off (#132)."""
    longer = max(len(one), len(other))
    if longer == 0:
        return True
    # One more edit than the floor allows, so the decision is always taken
    # by `similarity`'s own arithmetic on the exact distance below.
    allowed = int((1 - floor) * longer) + 1
    if abs(len(one) - len(other)) > allowed:
        return False
    previous = list(range(len(other) + 1))
    for row, left in enumerate(one, start=1):
        current = [row]
        for column, right in enumerate(other, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        if min(current) > allowed:
            return False
        previous = current
    return 1 - previous[-1] / longer >= floor
