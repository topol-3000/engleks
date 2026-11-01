from engleks_api.domain.diff import Hunk, reconcile
from engleks_api.domain.text_processing import Category, Change, ModelChange


def mc(
    original: str, replacement: str, category: Category = Category.GRAMMAR, explanation: str = "e"
) -> ModelChange:
    return ModelChange(original, replacement, category, explanation)


def test_exact_match():
    r = reconcile([Hunk((3, 5), "go", "goes")], [mc("go", "goes", explanation="Third person.")])
    assert r.changes == [Change("go", "goes", Category.GRAMMAR, "Third person.", (3, 5))]
    assert (r.matched, r.unexplained, r.dropped) == (1, 0, 0)


def test_containment_with_extra_context_case_and_spacing():
    r = reconcile([Hunk((3, 5), "in", "on")], [mc("In  Monday", "on monday", Category.WORD_CHOICE)])
    assert r.changes[0].category is Category.WORD_CHOICE


def test_empty_side_matches_only_empty_side():
    hunk = Hunk((7, 7), "", "a")
    assert reconcile([hunk], [mc("cat", "a cat")]).dropped == 1
    assert (
        reconcile([hunk], [mc("", "a", Category.ARTICLES)]).changes[0].category is Category.ARTICLES
    )


def test_unexplained_hunk_becomes_other():
    r = reconcile([Hunk((0, 2), "Hi", "Hello")], [])
    assert r.changes == [Change("Hi", "Hello", Category.OTHER, None, (0, 2))]
    assert (r.matched, r.unexplained, r.dropped) == (0, 1, 0)


def test_invented_change_dropped():
    r = reconcile([], [mc("dog", "cat")])
    assert (r.changes, r.dropped) == ([], 1)


def test_two_model_changes_in_one_hunk():
    hunk = Hunk((2, 12), "go to home", "went home")
    r = reconcile(
        [hunk],
        [
            mc("go", "went", Category.GRAMMAR, "Past tense."),
            mc("to home", "home", Category.WORD_CHOICE, "No 'to' before home."),
        ],
    )
    assert r.changes == [
        Change(
            "go to home", "went home", Category.GRAMMAR, "Past tense. No 'to' before home.", (2, 12)
        )
    ]


def test_repeated_words_prefer_unclaimed_hunks():
    hunks = [Hunk((0, 1), "a", "the"), Hunk((10, 11), "a", "the")]
    explained = reconcile(hunks, [mc("a", "the", explanation="1"), mc("a", "the", explanation="2")])
    assert [c.explanation for c in explained.changes] == ["1", "2"]
    assert [c.category for c in reconcile(hunks, [mc("a", "the")]).changes] == [
        Category.GRAMMAR,
        Category.OTHER,
    ]


def test_result_sorted_by_span_when_model_lists_out_of_order():
    hunks = [Hunk((0, 2), "go", "goes"), Hunk((10, 13), "cat", "cats")]
    r = reconcile(hunks, [mc("cat", "cats", explanation="b"), mc("go", "goes", explanation="a")])
    assert [c.explanation for c in r.changes] == ["a", "b"]
