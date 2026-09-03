import argparse
import sys

sys.dont_write_bytecode = True

import _venv_reexec

_venv_reexec.ensure_venv(__file__)

from app.core.linux_desktop_bootstrap import apply_linux_desktop_defaults

apply_linux_desktop_defaults()

from app.core.logging_setup import setup_logging
from app.pipelines.cortes.pipeline import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SaaS de Cortes Virais — gera 5 clipes por bloco de 20 minutos."
    )
    p.add_argument(
        "video",
        nargs="+",
        help="Caminho(s) para o(s) vídeo(s) de entrada (um ou mais).",
    )
    p.add_argument(
        "--lang", default="pt", choices=["pt", "en"],
        help="Idioma de destino das legendas (padrão: pt)"
    )
    p.add_argument(
        "--position", default="bottom", choices=["bottom", "top"],
        help="Posição das legendas (padrão: bottom)"
    )
    p.add_argument("--font", default="Arial", help="Fonte das legendas")
    p.add_argument("--color", default="#FFFF00", help="Cor do texto em hex (padrão: #FFFF00)")
    p.add_argument("--bg-color", default="#000000", help="Cor de fundo em hex (padrão: #000000)")
    p.add_argument("--opacity", type=int, default=75, help="Opacidade do fundo 0-100 (padrão: 75)")
    p.add_argument(
        "--dub-en",
        action="store_true",
        help="Dubla o clipe em inglês (Edge-TTS) e substitui o áudio original por cima do vídeo.",
    )
    p.add_argument(
        "--dub-pt",
        action="store_true",
        help="Dubla o clipe em português (Edge-TTS) e substitui o áudio original por cima do vídeo.",
    )
    p.add_argument(
        "--tts-voice",
        default=None,
        metavar="VOICE",
        help="Voz Edge-TTS (ex.: en-US-AriaNeural / pt-BR-AntonioNeural). Padrão: EDGE_TTS_VOICE(_PT) no .env.",
    )
    return p


def main() -> None:
    setup_logging()
    args = _build_parser().parse_args()

    dub_to = None
    if args.dub_en and args.dub_pt:
        raise SystemExit("Escolha apenas uma dublagem: --dub-en OU --dub-pt")
    if args.dub_en:
        dub_to = "en"
    elif args.dub_pt:
        dub_to = "pt"

    results = run_pipeline(
        video_path=args.video,
        target_language=args.lang,
        posicao=args.position,
        fonte=args.font,
        cor_letra=args.color,
        cor_fundo=args.bg_color,
        opacidade=args.opacity,
        dub_to=dub_to,
        tts_voice=args.tts_voice,
    )

    print("\nVídeos gerados:")
    for path in results:
        print(f"  -> {path}")


if __name__ == "__main__":
    main()
