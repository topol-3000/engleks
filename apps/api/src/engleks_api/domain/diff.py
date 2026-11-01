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


def diff(source: str, target: str) -> list[Hunk]:
    """Word-level edits turning `source` into `target`, in source order."""
    a = _tokenize(source)
    b = _tokenize(target)
    # autojunk would treat tokens like "the" and "," as junk on texts of ~200+ tokens.
    matcher = difflib.SequenceMatcher(None, [t.key for t in a], [t.key for t in b], autojunk=False)
    hunks: list[Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:
            p = a[i1].start if i1 < len(a) else len(source)
            span = (p, p)
        else:
            span = (a[i1].start, a[i2 - 1].end)
        hunks.append(Hunk(span, _slice(source, a[i1:i2]), _slice(target, b[j1:j2])))
    return hunks


@dataclass(frozen=True)
class Reconciliation:
    changes: list[Change]
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


def reconcile(hunks: Sequence[Hunk], model_changes: Sequence[ModelChange]) -> Reconciliation:
    """Explain each real edit with the model's claims. The diff is the truth."""
    claims: list[list[ModelChange]] = [[] for _ in hunks]
    dropped = 0
    for change in model_changes:
        candidates = [i for i, hunk in enumerate(hunks) if _matches(change, hunk)]
        if not candidates:
            dropped += 1
            continue
        unclaimed = [i for i in candidates if not claims[i]]
        claims[(unclaimed or candidates)[0]].append(change)

    changes: list[Change] = []
    for hunk, claimed in zip(hunks, claims, strict=True):
        if claimed:
            category = claimed[0].category
            explanation: str | None = " ".join(c.explanation for c in claimed)
        else:
            category, explanation = Category.OTHER, None
        changes.append(Change(hunk.original, hunk.replacement, category, explanation, hunk.span))
    changes.sort(key=lambda c: c.span[0] if c.span else 0)
    matched = sum(1 for claimed in claims if claimed)
    return Reconciliation(changes, matched, len(hunks) - matched, dropped)
