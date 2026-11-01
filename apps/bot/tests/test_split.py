from engleks_bot.rendering.split import split_message


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def test_short_text_single_chunk():
    assert split_message("hello") == ["hello"]


def test_empty_and_blank():
    assert split_message("") == []
    assert split_message("   \n") == []


def test_hard_split():
    assert [len(c) for c in split_message("a" * 5000)] == [4096, 904]


def test_prefers_newline():
    assert split_message("a" * 4000 + "\n" + "b" * 200) == ["a" * 4000 + "\n", "b" * 200]


def test_prefers_space_without_newline():
    assert split_message("a" * 4000 + " " + "b" * 200) == ["a" * 4000 + " ", "b" * 200]


def test_trailing_whitespace_not_sent_alone():
    assert split_message("a" * 4096 + "\n") == ["a" * 4096]


def test_astral_chars_counted_as_two_units():
    chunks = split_message("😀" * 3000)
    assert all(u16(c) <= 4096 for c in chunks)
    assert chunks[0] == "😀" * 2048
    assert "".join(chunks) == "😀" * 3000


def test_utf16_len():
    from engleks_bot.rendering.split import utf16_len

    assert utf16_len("a😀") == 3
    assert utf16_len("") == 0
