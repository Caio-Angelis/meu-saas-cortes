@echo off
setlocal EnableExtensions

pushd "%~dp0" || exit /b 1

set "LINKS_FILE=%~dp0links.txt"
if not exist "%LINKS_FILE%" goto :missing_links

where ffmpeg >nul 2>nul
if errorlevel 1 goto :missing_ffmpeg

rem Use yt-dlp if available; fallback to python module.
where yt-dlp >nul 2>nul
if errorlevel 1 goto :pick_python
set "YTDLP=yt-dlp"
goto :run

:pick_python
where python >nul 2>nul
if errorlevel 1 goto :missing_ytdlp_python
set "YTDLP=python -m yt_dlp"

echo Usando: %YTDLP%
echo Lendo:  "%LINKS_FILE%"
echo.

:run
%YTDLP% -a "%LINKS_FILE%" --restrict-filenames --windows-filenames -f "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" --merge-output-format mp4 -o "%%(title).200B_%%(id)s.%%(ext)s" --exec "ffmpeg -i \"%%(filepath)s\" -c copy -map 0 -segment_time 1800 -reset_timestamps 1 -f segment \"%%(id)s_parte%%03d.mp4\"" --exec "del \"%%(filepath)s\""
if errorlevel 1 goto :ytdlp_failed

popd
endlocal
exit /b 0

:missing_links
echo ERRO: Nao achei o arquivo "%LINKS_FILE%".
echo Dica: coloque o links.txt na mesma pasta deste .bat
echo.
pause
popd
endlocal
exit /b 2

:missing_ffmpeg
echo ERRO: ffmpeg nao encontrado no PATH.
echo Instale o FFmpeg ou coloque ffmpeg.exe no PATH.
echo.
pause
popd
endlocal
exit /b 3

:missing_ytdlp_python
echo ERRO: nao encontrei "yt-dlp" nem "python" no PATH.
echo Solucao 1: instale yt-dlp e deixe no PATH.
echo Solucao 2: instale Python e depois: pip install -U yt-dlp
echo.
pause
popd
endlocal
exit /b 4

:ytdlp_failed
echo.
echo ERRO: yt-dlp falhou (exit code %ERRORLEVEL%).
echo Se apareceu erro de YouTube/JS runtime, instale Deno (recomendado) ou configure --js-runtimes.
echo.
pause
popd
endlocal
exit /b 5