"""Validação e cálculo de cron — Fase A4.

Wrapper fino sobre `croniter`. Duas funções públicas:

- ``is_valid_cron(expr)``: True se for cron válido de **5 campos**.
- ``calculate_next_run(expr, now, start_date=None, end_date=None)``: próximo
  disparo (tz-aware UTC), respeitando a janela `[start_date, end_date]`.

Decisões:

- ``croniter`` aceita expressões com 5, 6 e 7 campos (segundo, minuto, hora,
  dia, mês, dia da semana, ano). Forçamos 5 para alinhar com o portal e com
  o cron clássico do Unix.
- Saída sempre tz-aware UTC: croniter pode devolver naive se entrada for
  naive — normalizamos.
- Quando ``start_date`` está no futuro, deslocamos a base do croniter para
  ``start_date``: a primeira ocorrência sai do início da janela, não do
  ``now``.
- Quando ``end_date`` está no passado (ou o próximo run fica após o
  ``end_date``), retornamos ``None`` — clipping fora da janela.
"""

from datetime import datetime, timezone
from typing import Optional

from croniter import CroniterBadCronError, croniter

_EXPECTED_FIELDS = 5


def is_valid_cron(expr: str) -> bool:
    """Valida uma expressão cron de 5 campos.

    Aceita:
      - estrelas / wildcards: ``*``
      - números: ``0 8 * * *``
      - listas: ``0 8,12 * * 1,3,5``
      - ranges: ``0 9-17 * * *``
      - steps: ``*/15 * * * *`` e ``0 9-17/2 * * *``
    """
    if not isinstance(expr, str):
        return False
    expr = expr.strip()
    if not expr:
        return False
    parts = expr.split()
    if len(parts) != _EXPECTED_FIELDS:
        return False
    try:
        croniter(expr)
    except (CroniterBadCronError, ValueError, KeyError):
        return False
    return True


def calculate_next_run(
    expr: str,
    now: datetime,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Optional[datetime]:
    """Próximo disparo de ``expr`` a partir de ``now``.

    - ``start_date`` no futuro empurra a base do croniter para ``start_date``.
    - ``end_date`` no passado (ou próximo run > ``end_date``) → ``None``.
    - Output é sempre tz-aware em UTC.
    """
    if not is_valid_cron(expr):
        return None

    # Garantir tz-aware UTC na entrada para croniter
    base = _to_utc_aware(now)
    if start_date is not None:
        start_aware = _to_utc_aware(start_date)
        if start_aware > base:
            base = start_aware

    if end_date is not None:
        end_aware = _to_utc_aware(end_date)
        if end_aware <= base:
            return None
    else:
        end_aware = None

    try:
        nxt = croniter(expr, base).get_next(datetime)
    except (CroniterBadCronError, ValueError):
        return None

    nxt = _to_utc_aware(nxt)
    if end_aware is not None and nxt > end_aware:
        return None
    return nxt


def _to_utc_aware(dt: datetime) -> datetime:
    """Normaliza um datetime para tz-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
