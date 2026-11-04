from engleks_api.domain.access import INVITE_ALPHABET, INVITE_CODE_LENGTH, generate_invite_code


def test_code_is_12_chars_from_the_alphabet():
    for _ in range(200):
        code = generate_invite_code()
        assert len(code) == 12 and set(code) <= set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")


def test_constants_match_the_spec():
    assert INVITE_ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    assert INVITE_CODE_LENGTH == 12


def test_codes_differ():
    assert len({generate_invite_code() for _ in range(50)}) == 50
