import difflib
import re
from dataclasses import dataclass

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
