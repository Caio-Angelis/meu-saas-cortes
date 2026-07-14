#!/usr/bin/env bash
# Apaga arquivos de temp/ com mais de 2 dias. NÃO toca em resultados/.
find temp/ -type f -mtime +2 -delete 2>/dev/null
find temp/ -type d -empty -delete 2>/dev/null
echo "temp/ limpo."
