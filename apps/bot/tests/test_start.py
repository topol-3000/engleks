from engleks_bot.handlers.start import HELP_TEXT, start


async def test_start_sends_help(make_message, sent):
    await start(make_message("/start"))
    assert [m.text for m in sent()] == [HELP_TEXT]


async def test_help_text_is_exact():
    assert HELP_TEXT == (
        "Hi! I'm Engleks, your English writing trainer.\n\n"
        "Send me a message in English and I'll correct it, or write in Ukrainian or Russian "
        "and I'll translate it into English. You'll also get short lessons on the patterns "
        "behind the edits."
    )
