import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from engleks_api.domain.text_processing import Category, Change, ModelChange

# Words (contractions kept whole, straight or curly apostrophe) or single punctuation marks.
# Whitespace is never a token, so whitespace-only edits produce no hunk.
_CURLY_APOSTROPHE = "\u2019"
_TOKEN = re.compile(r"\w+(?:['\u2019]\w+)*|[^\w\s]")


@dataclass(frozen=True)
class Hunk:
    span: tuple[int, int]
    original: str
    replacement: str
    target_span: tuple[int, int]


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    key: str


def _tokenize(text: str) -> list[_Token]:
    # A phone's curly apostrophe straightened by the model is not an edit.
    return [
        _Token(m.start(), m.end(), m.group().replace(_CURLY_APOSTROPHE, "'"))
        for m in _TOKEN.finditer(text)
    ]


def _slice(text: str, tokens: list[_Token]) -> str:
    return text[tokens[0].start : tokens[-1].end] if tokens else ""


def _span(text: str, tokens: list[_Token], i1: int, i2: int) -> tuple[int, int]:
    if i1 == i2:
        p = tokens[i1].start if i1 < len(tokens) else len(text)
        return (p, p)
    return (tokens[i1].start, tokens[i2 - 1].end)


def diff(source: str, target: str) -> list[Hunk]:
    """Word-level edits turning `source` into `target`, in source order."""
    a = _tokenize(source)
    b = _tokenize(target)
    # autojunk would treat tokens like "the" and "," as junk on texts of ~200+ tokens.
    matcher = difflib.SequenceMatcher(None, [t.key for t in a], [t.key for t in b], autojunk=False)
    return [
        Hunk(
            _span(source, a, i1, i2),
            _slice(source, a[i1:i2]),
            _slice(target, b[j1:j2]),
            _span(target, b, j1, j2),
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


@dataclass(frozen=True)
class Reconciliation:
    changes: list[Change]
    hunks: int
    matched: int
    unexplained: int
    dropped: int


def _normalize(s: str) -> str:
    return " ".join(s.casefold().split())


def _agree(model_side: str, hunk_side: str) -> bool:
    # Models quote a little more or less context than the diff; an empty side
    # matches only an empty side, or "" would match everything.
    m, h = _normalize(model_side), _normalize(hunk_side)
    if not m or not h:
        return not m and not h
    return m in h or h in m


def _matches(change: ModelChange, hunk: Hunk) -> bool:
    return _agree(change.original, hunk.original) and _agree(change.replacement, hunk.replacement)


type _Range = tuple[int, int]  # hunks[i:j]


def _trim(text: str, span: tuple[int, int]) -> tuple[int, int]:
    start, end = span
    chunk = text[start:end]
    start += len(chunk) - len(chunk.lstrip())
    return start, max(start, end - (len(chunk) - len(chunk.rstrip())))


def _merge(source: str, target: str, hunks: Sequence[Hunk]) -> Hunk:
    """Consecutive hunks as one, with the unchanged words between them."""
    span = _trim(source, (hunks[0].span[0], hunks[-1].span[1]))
    target_span = _trim(target, (hunks[0].target_span[0], hunks[-1].target_span[1]))
    return Hunk(
        span, source[span[0] : span[1]], target[target_span[0] : target_span[1]], target_span
    )


def _covers(start: int, end: int, hunk: Hunk, replacement: str) -> bool:
    h_start, h_end = hunk.span
    if h_start < h_end:
        return h_start < end and h_end > start
    # An insertion at the edge of a quote belongs to it only if the replacement has it there.
    inserted, quoted = _normalize(hunk.replacement), _normalize(replacement)
    if h_start == start:
        return quoted.startswith(inserted)
    if h_start == end:
        return quoted.endswith(inserted)
    return start < h_start < end


def _quotes(model_side: str, hunk_side: str) -> bool:
    # The position is already pinned by the original, so an empty hunk side is fine:
    # "add a limit" quotes the insertion "a", "like" quotes the deletion of "very".
    m, h = _normalize(model_side), _normalize(hunk_side)
    return h in m or m in h if m else not h


def _locate(
    source: str,
    target: str,
    hunks: Sequence[Hunk],
    bounds: tuple[set[int], set[int]],
    change: ModelChange,
) -> list[_Range]:
    """Hunks covered by each word-aligned occurrence of the model's exact quote."""
    fragment = change.original.strip()
    starts, ends = bounds
    ranges: list[_Range] = []
    start = source.find(fragment) if fragment else -1
    while start != -1:
        end = start + len(fragment)
        covered = [
            i for i, hunk in enumerate(hunks) if _covers(start, end, hunk, change.replacement)
        ]
        if start in starts and end in ends and covered:
            r = (covered[0], covered[-1] + 1)
            merged = _merge(source, target, hunks[r[0] : r[1]])
            if _quotes(change.replacement, merged.replacement):
                ranges.append(r)
        start = source.find(fragment, start + 1)
    return ranges


def reconcile(source: str, target: str, model_changes: Sequence[ModelChange]) -> Reconciliation:
    """Explain each real edit with the model's claims.

    The diff is the truth: every edit is shown, explained or not. The model decides the
    grouping: a claim quoting the input exactly turns the hunks it covers into one change.
    """
    hunks = diff(source, target)
    tokens = _tokenize(source)
    bounds = ({t.start for t in tokens}, {t.end for t in tokens})
    located = [(c, _locate(source, target, hunks, bounds, c)) for c in model_changes]
    # Widest first, so a phrase rewrite groups its hunks even if a claim inside it comes first.
    located.sort(key=lambda cr: -max((j - i for i, j in cr[1]), default=0))

    claims: dict[_Range, list[ModelChange]] = {}
    unlocated: list[ModelChange] = []
    for change, ranges in located:
        disjoint = [r for r in ranges if all(r[1] <= i or r[0] >= j for i, j in claims)]
        same = [r for r in ranges if r in claims]
        if disjoint or same:
            claims.setdefault((disjoint or same)[0], []).append(change)
        else:
            unlocated.append(change)

    grouped = {k for i, j in claims for k in range(i, j)}
    for k in range(len(hunks)):
        if k not in grouped:
            claims[(k, k + 1)] = []
    units = {r: _merge(source, target, hunks[r[0] : r[1]]) for r in sorted(claims)}

    dropped = 0
    for change in unlocated:
        candidates = [r for r, unit in units.items() if _matches(change, unit)]
        if not candidates:
            dropped += 1
            continue
        unclaimed = [r for r in candidates if not claims[r]]
        claims[(unclaimed or candidates)[0]].append(change)

    changes: list[Change] = []
    for r, unit in units.items():
        if claimed := claims[r]:
            category = claimed[0].category
            explanation: str | None = " ".join(c.explanation for c in claimed)
        else:
            category, explanation = Category.OTHER, None
        changes.append(Change(unit.original, unit.replacement, category, explanation, unit.span))
    matched = sum(1 for r in units if claims[r])
    return Reconciliation(changes, len(hunks), matched, len(units) - matched, dropped)
