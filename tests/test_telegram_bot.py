from telegram_bot import _command_body, _parse_batalha_args, _parse_quiz_args


def test_telegram_command_parsers():
    assert _command_body("/tts@meu_bot Olá mundo", "tts") == "Olá mundo"
    assert _parse_quiz_args("/quiz Geografia 2 3") == ("Geografia", 2, 3.0)
    assert _parse_batalha_args("/batalha plinko Batman vs Superman") == (
        "Batman vs Superman",
        "plinko",
    )
    assert _parse_batalha_args("/batalha Batman vs Superman") == (
        "Batman vs Superman",
        "tamanho",
    )
