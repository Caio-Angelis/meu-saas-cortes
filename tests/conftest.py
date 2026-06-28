from __future__ import annotations

import sys
from pathlib import Path

# Garante que `app/` seja importável nos testes (sem depender de instalação do pacote).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

