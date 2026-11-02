from engleks_api.domain.diff import reconcile
from engleks_api.domain.text_processing import Category, Change, ModelChange


def mc(
    original: str, replacement: str, category: Category = Category.GRAMMAR, explanation: str = "e"
) -> ModelChange:
    return ModelChange(original, replacement, category, explanation)


def test_exact_match():
    r = reconcile("He go home.", "He goes home.", [mc("go", "goes", explanation="Third person.")])
    assert r.changes == [Change("go", "goes", Category.GRAMMAR, "Third person.", (3, 5))]
    assert (r.hunks, r.matched, r.unexplained, r.dropped) == (1, 1, 0, 0)


def test_phrase_rewrite_spanning_several_hunks_is_one_change():
    source = "we have to add limit in bot for input number of characters as 1k."
    target = "We have to add a 1,000-character input limit to the bot."
    phrase = "limit in bot for input number of characters as 1k"
    r = reconcile(
        source,
        target,
        [
            mc("we", "We", Category.SPELLING, "Capital."),
            mc(phrase, "a 1,000-character input limit to the bot", Category.WORD_ORDER, "Order."),
        ],
    )
    assert r.changes == [
        Change("we", "We", Category.SPELLING, "Capital.", (0, 2)),
        Change(
            phrase,
            "a 1,000-character input limit to the bot",
            Category.WORD_ORDER,
            "Order.",
            (15, 64),
        ),
    ]
    assert (r.hunks, r.matched, r.unexplained, r.dropped) == (4, 2, 0, 0)


def test_claim_inside_a_phrase_rewrite_joins_it_whatever_the_order():
    source = "we have to add limit in bot for input number of characters as 1k."
    target = "we have to add a 1,000-character input limit to the bot."
    phrase = mc("limit in bot for input number of characters as 1k", target[15:-1], explanation="b")
    r = reconcile(source, target, [mc("in", "to the", explanation="a"), phrase])
    assert [(c.span, c.explanation) for c in r.changes] == [((15, 64), "b a")]
    assert (r.matched, r.dropped) == (1, 0)


def test_insertions_quoted_with_context():
    r = reconcile(
        "we need add limit",
        "we need to add a limit",
        [
            mc("need add", "need to add", explanation="Infinitive."),
            mc("add limit", "add a limit", Category.ARTICLES, "Countable noun."),
        ],
    )
    assert r.changes == [
        Change("", "to", Category.GRAMMAR, "Infinitive.", (8, 8)),
        Change("", "a", Category.ARTICLES, "Countable noun.", (12, 12)),
    ]


def test_deletion_quoted_with_context():
    r = reconcile("I very like it.", "I like it.", [mc("very like", "like", Category.STYLE)])
    assert r.changes == [Change("very", "", Category.STYLE, "e", (2, 6))]


def test_adjacent_fixes_in_one_hunk_share_it():
    r = reconcile(
        "I has a apple",
        "I have an apple",
        [mc("has", "have", explanation="Agreement."), mc("a", "an", explanation="Vowel.")],
    )
    assert r.changes == [Change("has a", "have an", Category.GRAMMAR, "Agreement. Vowel.", (2, 7))]


def test_unlocated_claim_falls_back_to_containment_ignoring_case_and_spacing():
    r = reconcile(
        "We meet in Monday",
        "We meet on Monday",
        [mc("In  Monday", "on monday", Category.WORD_CHOICE)],
    )
    assert r.changes[0].category is Category.WORD_CHOICE


def test_empty_side_matches_only_empty_side():
    assert reconcile("He go home", "He goes home", [mc("", "goes")]).dropped == 1
    r = reconcile("I have cat", "I have a cat", [mc("", "a", Category.ARTICLES)])
    assert r.changes[0].category is Category.ARTICLES


def test_located_claim_with_wrong_replacement_is_dropped():
    r = reconcile("He go home.", "He goes home.", [mc("go home", "went home")])
    assert r.changes == [Change("go", "goes", Category.OTHER, None, (3, 5))]
    assert r.dropped == 1


def test_unexplained_hunk_becomes_other():
    r = reconcile("Hi there", "Hello there", [])
    assert r.changes == [Change("Hi", "Hello", Category.OTHER, None, (0, 2))]
    assert (r.matched, r.unexplained, r.dropped) == (0, 1, 0)


def test_invented_change_dropped():
    r = reconcile("Fine.", "Fine.", [mc("dog", "cat")])
    assert (r.changes, r.dropped) == ([], 1)


def test_repeated_words_prefer_unclaimed_hunks():
    source, target = "a cat and a dog", "the cat and the dog"
    explained = reconcile(
        source, target, [mc("a", "the", explanation="1"), mc("a", "the", explanation="2")]
    )
    assert [(c.span, c.explanation) for c in explained.changes] == [((0, 1), "1"), ((10, 11), "2")]
    assert [c.category for c in reconcile(source, target, [mc("a", "the")]).changes] == [
        Category.GRAMMAR,
        Category.OTHER,
    ]


def test_result_in_source_order_when_model_lists_out_of_order():
    r = reconcile(
        "he go and cat run",
        "he goes and cats run",
        [mc("cat", "cats", explanation="b"), mc("go", "goes", explanation="a")],
    )
    assert [c.explanation for c in r.changes] == ["a", "b"]
