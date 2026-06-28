#!/usr/bin/env bash
# Equivalente Linux de baixarlinks.bat — baixa URLs de links.txt com yt-dlp e parte em segmentos de 30 min.
#
# YouTube (avisos comuns):
#   • Cookies Chrome no Linux: pip install secretstorage no .venv (já está no requirements.txt).
#   • Runtime JS: Deno no PATH → --js-runtimes deno; sempre → --remote-components ejs:github.
#   • Cookies de sessão: o script tenta --cookies-from-browser
#     automaticamente se achar Chrome/Chromium/Firefox/Brave no sistema.
#     Para forçar: export YTDLP_COOKIES_BROWSER=chrome
#     Para desativar auto: export YTDLP_NO_AUTO_COOKIES=1
#   • Arquivo: ./youtube_cookies.txt (ou defina YTDLP_COOKIES_FILE) — formato Netscape
#
set -euo pipefail

# Deno (JS YouTube) costuma estar aqui; yt-dlp só acha se estiver no PATH.
[[ -d "$HOME/.deno/bin" ]] && export PATH="$HOME/.deno/bin:$PATH"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Bibliotecas Python do próprio venv (ex.: secretstorage p/ cookies Chrome no Linux).
[[ -d "$DIR/.venv/bin" ]] && export PATH="$DIR/.venv/bin:$PATH"

LINKS_FILE="$DIR/links.txt"
if [[ ! -f "$LINKS_FILE" ]]; then
  echo "ERRO: Não achei o arquivo \"$LINKS_FILE\"."
  echo "Dica: coloque o links.txt na mesma pasta deste script."
  echo
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ERRO: ffmpeg não encontrado no PATH."
  echo "Instale o FFmpeg (ex.: sudo apt install ffmpeg) ou use um binário no PATH."
  echo
  exit 3
fi

pick_ytdlp() {
  if [[ -x "$DIR/.venv/bin/yt-dlp" ]]; then
    echo "$DIR/.venv/bin/yt-dlp"
    return 0
  fi
  if command -v yt-dlp >/dev/null 2>&1; then
    command -v yt-dlp
    return 0
  fi
  if [[ -x "$DIR/.venv/bin/python" ]]; then
    echo "venv_python_module"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3_module"
    return 0
  fi
  return 1
}

YTDLP_CMD=""
if ! YTDLP_CMD="$(pick_ytdlp)"; then
  echo "ERRO: não encontrei yt-dlp nem Python no PATH."
  echo "Solução 1: instale yt-dlp e deixe no PATH."
  echo "Solução 2: crie o .venv do projeto e instale as dependências (pip install -r requirements.txt)."
  echo
  exit 4
fi

# Nome aceito pelo yt-dlp em --cookies-from-browser (ver yt-dlp --help).
guess_cookies_browser() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    [[ -d "/Applications/Google Chrome.app" ]] && echo chrome && return
    [[ -d "/Applications/Chromium.app" ]] && echo chromium && return
    [[ -d "/Applications/Brave Browser.app" ]] && echo brave && return
    [[ -d "/Applications/Firefox.app" ]] && echo firefox && return
    return 1
  fi
  command -v google-chrome-stable &>/dev/null && echo chrome && return
  command -v google-chrome &>/dev/null && echo chrome && return
  command -v chromium-browser &>/dev/null && echo chromium && return
  command -v chromium &>/dev/null && echo chromium && return
  command -v brave-browser &>/dev/null && echo brave && return
  command -v firefox &>/dev/null && echo firefox && return
  return 1
}

case "$YTDLP_CMD" in
  venv_python_module) echo "Usando: $DIR/.venv/bin/python -m yt_dlp" ;;
  python3_module)     echo "Usando: python3 -m yt_dlp" ;;
  *)                  echo "Usando: $YTDLP_CMD" ;;
esac
echo "Lendo:  \"$LINKS_FILE\""
echo

# Argumentos extras para extratores que dependem de JS / sessão (YouTube).
YTDLP_EXTRA=()
# Solver de challenges JS do YouTube (“n”); sem isso costuma sobrar só thumbnail / “format not available”.
YTDLP_EXTRA+=(--remote-components ejs:github)
echo "YouTube: --remote-components ejs:github"
if command -v deno >/dev/null 2>&1; then
  YTDLP_EXTRA+=(--js-runtimes deno)
  echo "Deno: --js-runtimes deno"
fi

COOKIES_FILE="${YTDLP_COOKIES_FILE:-$DIR/youtube_cookies.txt}"
_AUTO_BROWSER=""
if [[ -n "${YTDLP_COOKIES_BROWSER:-}" ]]; then
  YTDLP_EXTRA+=(--cookies-from-browser "${YTDLP_COOKIES_BROWSER}")
  echo "Cookies: --cookies-from-browser ${YTDLP_COOKIES_BROWSER} (YTDLP_COOKIES_BROWSER)"
elif [[ -f "$COOKIES_FILE" ]]; then
  YTDLP_EXTRA+=(--cookies "$COOKIES_FILE")
  echo "Cookies: arquivo $COOKIES_FILE"
elif [[ -z "${YTDLP_NO_AUTO_COOKIES:-}" ]] && _AUTO_BROWSER="$(guess_cookies_browser || true)" && [[ -n "$_AUTO_BROWSER" ]]; then
  YTDLP_EXTRA+=(--cookies-from-browser "${_AUTO_BROWSER}")
  echo "Cookies: --cookies-from-browser ${_AUTO_BROWSER} (detecção automática)"
  echo "  (login no YouTube nesse navegador ajuda. Override: YTDLP_COOKIES_BROWSER=… Desligar: YTDLP_NO_AUTO_COOKIES=1)"
elif [[ -n "${YTDLP_ALLOW_NO_COOKIES:-}" ]]; then
  echo "Aviso: sem cookies (YTDLP_ALLOW_NO_COOKIES=1). Falhas no YouTube são esperadas."
else
  echo "ERRO: não achei navegador para cookies nem $COOKIES_FILE."
  echo "  O YouTube costuma exigir cookies (login). Opções:"
  echo "    • Instale Chrome ou Firefox e faça login no youtube.com, depois rode de novo este script (detecção automática)."
  echo "    • export YTDLP_COOKIES_BROWSER=chrome   # ou firefox, chromium…"
  echo "    • Ou crie $COOKIES_FILE — https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
  echo "    • Para tentar mesmo assim: YTDLP_ALLOW_NO_COOKIES=1 ./baixarlinks.sh"
  exit 6
fi

if ! command -v deno >/dev/null 2>&1; then
  echo "Aviso: Deno não está no PATH (recomendado p/ YouTube). Instale: curl -fsSL https://deno.land/install.sh | sh"
fi
echo

YTDLP_ARGS=(
  "${YTDLP_EXTRA[@]}"
  -a "$LINKS_FILE"
  --restrict-filenames
  -f "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
  --merge-output-format mp4
  -o "%(title).200B_%(id)s.%(ext)s"
  --exec 'ffmpeg -i "%(filepath)s" -c copy -map 0 -segment_time 1800 -reset_timestamps 1 -f segment "%(id)s_parte%03d.mp4"'
  --exec 'rm -f "%(filepath)s"'
)

set +e
case "$YTDLP_CMD" in
  venv_python_module)
    "$DIR/.venv/bin/python" -m yt_dlp "${YTDLP_ARGS[@]}"
    ;;
  python3_module)
    python3 -m yt_dlp "${YTDLP_ARGS[@]}"
    ;;
  *)
    "$YTDLP_CMD" "${YTDLP_ARGS[@]}"
    ;;
esac
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  echo
  echo "ERRO: yt-dlp falhou (exit code $rc)."
  echo
  echo "YouTube (checklist):"
  echo "  1) No .venv: pip install -r requirements.txt  (inclui secretstorage p/ cookies do Chrome no Linux)"
  echo "  2) Deno no PATH: curl -fsSL https://deno.land/install.sh | sh  e  export PATH=\"\$HOME/.deno/bin:\$PATH\""
  echo "  3) Login no YouTube no Chrome/Firefox; o script usa cookies do navegador automaticamente"
  echo "  • EJS: https://github.com/yt-dlp/yt-dlp/wiki/EJS"
  echo "  • Cookies: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies"
  echo
  exit 5
fi

exit 0
