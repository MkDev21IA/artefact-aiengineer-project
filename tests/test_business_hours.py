"""Unit tests for deterministic store business hours in `America/Campo_Grande`."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from emporio_agent.domain.business_hours import BusinessHoursService
from emporio_agent.domain.models import StoreStatus

CG_TZ = ZoneInfo("America/Campo_Grande")
SP_TZ = ZoneInfo("America/Sao_Paulo")


def test_weekday_open_during_regular_hours() -> None:
    service = BusinessHoursService()
    # Tuesday 2026-11-03 at 10:30 AMT
    snap = service.get_snapshot(datetime(2026, 11, 3, 10, 30, tzinfo=CG_TZ))
    assert snap.status == StoreStatus.OPEN
    assert snap.day_of_week_en == "Tuesday"
    assert snap.day_of_week_pt_br == "Terça-feira"
    assert not snap.is_holiday


def test_weekday_boundary_open_at_0900_and_closed_at_1800() -> None:
    service = BusinessHoursService()
    snap_open = service.get_snapshot(datetime(2026, 11, 4, 9, 0, tzinfo=CG_TZ))
    snap_close = service.get_snapshot(datetime(2026, 11, 4, 18, 0, tzinfo=CG_TZ))
    assert snap_open.status == StoreStatus.OPEN
    assert snap_close.status == StoreStatus.CLOSED
    assert "amanhã" in snap_close.next_opening_pt_br


def test_weekday_early_morning_reports_opening_same_day() -> None:
    service = BusinessHoursService()
    snap = service.get_snapshot(datetime(2026, 11, 4, 8, 15, tzinfo=CG_TZ))
    assert snap.status == StoreStatus.CLOSED
    assert "hoje às 09:00" in snap.next_opening_pt_br


def test_saturday_open_morning_and_closed_afternoon() -> None:
    service = BusinessHoursService()
    sat_open = service.get_snapshot(datetime(2026, 11, 7, 12, 59, tzinfo=CG_TZ))
    sat_closed = service.get_snapshot(datetime(2026, 11, 7, 13, 0, tzinfo=CG_TZ))
    assert sat_open.status == StoreStatus.OPEN
    assert sat_closed.status == StoreStatus.CLOSED
    assert "segunda-feira" in sat_closed.next_opening_pt_br


def test_sunday_is_closed() -> None:
    service = BusinessHoursService()
    sun = service.get_snapshot(datetime(2026, 11, 8, 11, 0, tzinfo=CG_TZ))
    assert sun.status == StoreStatus.CLOSED
    assert "Sunday" in sun.status_reason
    assert "amanhã (segunda-feira" in sun.next_opening_pt_br


def test_national_holiday_finados_is_closed_even_on_monday() -> None:
    service = BusinessHoursService()
    # 2026-11-02 is Monday AND Finados holiday in Brazil
    finados = service.get_snapshot(datetime(2026, 11, 2, 11, 0, tzinfo=CG_TZ))
    assert finados.status == StoreStatus.CLOSED
    assert finados.is_holiday is True
    assert finados.holiday_name == "Finados"
    assert "terça-feira" in finados.next_opening_pt_br


def test_timezone_conversion_from_sao_paulo_prevents_one_hour_drift() -> None:
    service = BusinessHoursService()
    # 09:30 in Sao Paulo (UTC-3) is 08:30 in Campo Grande (UTC-4) -> store is still CLOSED!
    sp_dt = datetime(2026, 11, 3, 9, 30, tzinfo=SP_TZ)
    snap = service.get_snapshot(sp_dt)
    assert snap.status == StoreStatus.CLOSED
    assert snap.formatted_datetime_pt_br.endswith("08:30")


def test_custom_extra_holiday_is_respected() -> None:
    custom_day = date(2026, 4, 3)  # Good Friday (movable holiday example)
    service = BusinessHoursService(extra_holidays={custom_day: "Sexta-feira Santa"})
    snap = service.get_snapshot(datetime(2026, 4, 3, 10, 0, tzinfo=CG_TZ))
    assert snap.status == StoreStatus.CLOSED
    assert snap.holiday_name == "Sexta-feira Santa"
