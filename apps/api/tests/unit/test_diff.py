import pytest

from engleks_api.domain.diff import Hunk, diff

DONT_CURLY = "don\u2019t"  # a phone's curly apostrophe

CASES = [
    (
        "comma_insert",
        "Hi John how are you",
        "Hi John, how are you",
        [Hunk((8, 8), "", ",", (7, 8))],
    ),
    ("article_insert", "I have cat", "I have a cat", [Hunk((7, 7), "", "a", (7, 8))]),
    ("replacement", "He go home", "He goes home", [Hunk((3, 5), "go", "goes", (3, 7))]),
    ("deletion", "I very like it", "I like it", [Hunk((2, 6), "very", "", (2, 2))]),
    (
        "multi_token_keeps_user_spacing",
        "a  lot of people",
        "many people",
        [Hunk((0, 9), "a  lot of", "many", (0, 4))],
    ),
    (
        "contraction_one_token",
        "I dont know",
        "I don't know",
        [Hunk((2, 6), "dont", "don't", (2, 7))],
    ),
    (
        "curly_contraction_one_token",
        f"I {DONT_CURLY} know",
        f"I {DONT_CURLY} know.",
        [Hunk((12, 12), "", ".", (12, 13))],
    ),
    ("curly_vs_straight_is_no_change", f"I {DONT_CURLY} know", "I don't know", []),
    ("whitespace_only", "Hello  world", "Hello\nworld", []),
    (
        "repeated_word_second_occurrence",
        "a cat and a dog",
        "a cat and the dog",
        [Hunk((10, 11), "a", "the", (10, 13))],
    ),
    ("emoji_before_edit", "🙂 he go", "🙂 he goes", [Hunk((5, 7), "go", "goes", (5, 9))]),
    ("insert_at_end", "Thanks", "Thanks!", [Hunk((6, 6), "", "!", (6, 7))]),
    ("identical", "Fine.", "Fine.", []),
]


@pytest.mark.parametrize(
    ("source", "target", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_diff(source, target, expected):
    assert diff(source, target) == expected


def test_long_text_keeps_common_tokens_as_anchors():
    filler = "the cat sat on the mat , and " * 40  # 360 tokens, every one "popular"
    # Only "popular" tokens follow the edit, so nothing but them can anchor the tail.
    hunks = diff(filler + "he go " + filler, filler + "he goes " + filler)
    assert [(h.original, h.replacement) for h in hunks] == [("go", "goes")]
