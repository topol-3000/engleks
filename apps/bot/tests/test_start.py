from engleks_bot.handlers.start import HELP_TEXT, start


async def test_start_sends_help(make_message, sent):
    await start(make_message("/start"))
    assert [m.text for m in sent()] == [HELP_TEXT]


async def test_help_text_is_exact():
    assert HELP_TEXT.startswith("Hi! I'm Engleks, your English writing trainer.\n\nSend me")
    assert HELP_TEXT.endswith("rewrite the same text in that mode.")
