"""Testes do módulo `graphql_api.lib.cron` — Fase A4.

Valida:
  - `is_valid_cron` cobre 5 campos + ranges + steps + lists.
  - `calculate_next_run` retorna datetime tz-aware UTC, respeita
    `start_date` / `end_date` (janela).
"""

from datetime import datetime, timedelta, timezone

from graphql_api.lib.cron import calculate_next_run, is_valid_cron


class TestIsValidCron:
    def test_simple_5field_cron_valid(self):
        assert is_valid_cron("0 8 * * *") is True

    def test_every_minute_valid(self):
        assert is_valid_cron("* * * * *") is True

    def test_cron_validator_handles_step_and_range(self):
        """*/15 nos minutos, 9-17 nas horas, segunda-sexta (1-5)."""
        assert is_valid_cron("*/15 9-17 * * 1-5") is True

    def test_cron_with_list_valid(self):
        assert is_valid_cron("0 8,12,18 * * 1,3,5") is True

    def test_invalid_garbage_string(self):
        assert is_valid_cron("garbage") is False

    def test_invalid_empty_string(self):
        assert is_valid_cron("") is False

    def test_invalid_too_few_fields(self):
        assert is_valid_cron("0 8 *") is False

    def test_invalid_hour_out_of_range(self):
        assert is_valid_cron("0 99 * * *") is False


class TestCalculateNextRun:
    def test_calculate_next_run_daily_08(self):
        """schedule '0 8 * * *' chamado às 10:00 UTC → amanhã 08:00 UTC."""
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = calculate_next_run("0 8 * * *", now)
        assert result is not None
        assert result == datetime(2026, 1, 2, 8, 0, 0, tzinfo=timezone.utc)

    def test_calculate_next_run_before_today_returns_today(self):
        """schedule '0 8 * * *' chamado às 06:00 UTC → hoje 08:00 UTC."""
        now = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        result = calculate_next_run("0 8 * * *", now)
        assert result is not None
        assert result == datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

    def test_calculate_next_run_uses_utc(self):
        """Output sempre é tz-aware em UTC."""
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = calculate_next_run("0 8 * * *", now)
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_calculate_next_run_invalid_expr_returns_none(self):
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert calculate_next_run("garbage", now) is None

    def test_calculate_next_run_with_start_date_in_future(self):
        """startDate no futuro empurra o nextRunAt para depois de startDate."""
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        start = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = calculate_next_run("0 8 * * *", now, start_date=start)
        assert result is not None
        assert result >= start
        # Primeira ocorrência de '0 8 * * *' a partir de 2026-06-01 00:00 UTC.
        assert result == datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)

    def test_calculate_next_run_with_end_date_in_past_returns_none(self):
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = calculate_next_run("0 8 * * *", now, end_date=end)
        assert result is None

    def test_calculate_next_run_within_window_returns_value(self):
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        result = calculate_next_run("0 8 * * *", now, end_date=end)
        assert result is not None
        assert result < end
