"""How alike two codes, or two descriptions, are: normalized Levenshtein.

One minus the edit distance over the longer length, so identical texts score
1.0, texts with nothing in common 0.0, and the measure is a true distance:
it is symmetric and a single edit always costs the same share of the longer
text. Hand-written, since it is a few dozen lines and the alternative in the
standard library, `difflib.SequenceMatcher.ratio`, is not a distance, scores
unrelated codes higher, and switches on an autojunk heuristic at 200
characters (#77). Beside it a bounded check, whether a pair reaches a floor,
which stops as soon as the answer is no and so is cheap on pairs that are a
long way apart.

The texts are compared as the scorer normalizes them, whitespace collapsed
and case dropped; that is the caller's job, so these functions do one thing.
"""


def similarity(one: str, other: str) -> float:
    """1 minus edits over the longer length; two empty texts are alike."""
    longer = max(len(one), len(other))
    if longer == 0:
        return 1.0
    distance = _distance(one, other)
    assert distance is not None
    return 1 - distance / longer


def at_least(one: str, other: str, floor: float) -> bool:
    """Whether `similarity(one, other) >= floor`, decided without the whole
    table when the pair cannot reach the floor."""
    longer = max(len(one), len(other))
    if longer == 0:
        return True
    # One more edit than the floor allows, so a pair that reaches it always
    # gets its exact distance and the decision is `similarity`'s arithmetic.
    distance = _distance(one, other, int((1 - floor) * longer) + 1)
    return distance is not None and 1 - distance / longer >= floor


def _distance(one: str, other: str, bound: int | None = None) -> int | None:
    """The Levenshtein distance, two rows of the classic table at a time, or
    None as soon as it is sure to exceed `bound`: the lengths alone, or a row
    whose every cell is past it, since no later row can come back under."""
    if one == other:
        return 0
    if bound is not None and abs(len(one) - len(other)) > bound:
        return None
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
        if bound is not None and min(current) > bound:
            return None
        previous = current
    return previous[-1]
