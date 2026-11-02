"""Deterministic business-hours evaluation for Emporio da Musica (Campo Grande/MS).

Store schedule per `políticas_da_loja.pdf` (Section 2):
- Monday to Friday: 09:00 to 18:00
- Saturday: 09:00 to 13:00
- Sundays and Holidays: Closed
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from emporio_agent.domain.models import StoreOperationalSnapshot, StoreStatus

WEEKDAY_NAMES_EN: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

WEEKDAY_NAMES_PT_BR: tuple[str, ...] = (
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
)

# Fixed Brazilian national + Mato Grosso do Sul state holidays (month, day) -> name
FIXED_HOLIDAYS_BR_MS: dict[tuple[int, int], str] = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalhador",
    (8, 26): "Aniversário de Campo Grande",
    (9, 7): "Independência do Brasil",
    (10, 11): "Criação do Estado de Mato Grosso do Sul",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Dia Nacional de Zumbi e da Consciência Negra",
    (12, 25): "Natal",
}

WEEKDAY_OPEN = time(9, 0)
WEEKDAY_CLOSE = time(18, 0)
SATURDAY_OPEN = time(9, 0)
SATURDAY_CLOSE = time(13, 0)

SCHEDULE_SUMMARY = (
    "Monday-Friday 09:00-18:00, Saturday 09:00-13:00, Sundays and Holidays Closed"
)


class BusinessHoursService:
    """Evaluates live store operational status and next opening window deterministically."""

    def __init__(
        self,
        timezone_name: str = "America/Campo_Grande",
        respect_holidays: bool = True,
        extra_holidays: dict[date, str] | None = None,
    ) -> None:
        self._timezone_name = timezone_name
        self._tz = ZoneInfo(timezone_name)
        self._respect_holidays = respect_holidays
        self._extra_holidays = extra_holidays or {}

    @property
    def timezone_name(self) -> str:
        """Return the IANA timezone identifier used by this service."""
        return self._timezone_name

    def get_holiday_name(self, target_date: date) -> str | None:
        """Return the holiday name if `target_date` falls on a configured holiday."""
        if not self._respect_holidays:
            return None
        if target_date in self._extra_holidays:
            return self._extra_holidays[target_date]
        return FIXED_HOLIDAYS_BR_MS.get((target_date.month, target_date.day))

    def get_snapshot(self, now: datetime | None = None) -> StoreOperationalSnapshot:
        """Build a complete operational snapshot for `now` (defaults to current time)."""
        local_dt = (
            datetime.now(self._tz)
            if now is None
            else (now.astimezone(self._tz) if now.tzinfo else now.replace(tzinfo=self._tz))
        )

        weekday_idx = local_dt.weekday()
        day_en = WEEKDAY_NAMES_EN[weekday_idx]
        day_pt = WEEKDAY_NAMES_PT_BR[weekday_idx]
        local_time = local_dt.time().replace(tzinfo=None)
        holiday_name = self.get_holiday_name(local_dt.date())

        is_open, reason = self._evaluate_open_state(weekday_idx, local_time, holiday_name)
        next_opening_pt = (
            "Loja aberta neste momento"
            if is_open
            else self._compute_next_opening_pt_br(local_dt)
        )

        return StoreOperationalSnapshot(
            current_iso_datetime=local_dt.isoformat(timespec="seconds"),
            formatted_datetime_pt_br=local_dt.strftime("%d/%m/%Y %H:%M"),
            day_of_week_en=day_en,
            day_of_week_pt_br=day_pt,
            timezone=self._timezone_name,
            status=StoreStatus.OPEN if is_open else StoreStatus.CLOSED,
            is_holiday=holiday_name is not None,
            holiday_name=holiday_name,
            status_reason=reason,
            next_opening_pt_br=next_opening_pt,
            schedule_summary=f"{SCHEDULE_SUMMARY} ({self._timezone_name})",
        )

    def _evaluate_open_state(
        self,
        weekday_idx: int,
        local_time: time,
        holiday_name: str | None,
    ) -> tuple[bool, str]:
        """Return `(is_open, english_reason)` for the given local day and time."""
        if holiday_name is not None:
            return False, f"Closed for holiday ({holiday_name})"

        if weekday_idx == 6:
            return False, "Closed on Sundays"

        if weekday_idx == 5:
            if SATURDAY_OPEN <= local_time < SATURDAY_CLOSE:
                return True, "Within Saturday business hours (09:00-13:00)"
            return False, "Outside Saturday business hours (09:00-13:00)"

        if WEEKDAY_OPEN <= local_time < WEEKDAY_CLOSE:
            return True, "Within weekday business hours (09:00-18:00)"
        return False, "Outside weekday business hours (09:00-18:00)"

    def _compute_next_opening_pt_br(self, local_dt: datetime) -> str:
        """Find the next valid opening date/time in Brazilian Portuguese."""
        local_time = local_dt.time().replace(tzinfo=None)
        today = local_dt.date()

        # Check if the store still opens later today
        if self.get_holiday_name(today) is None:
            if today.weekday() < 5 and local_time < WEEKDAY_OPEN:
                return "hoje às 09:00 (horário de Campo Grande/MS)"
            if today.weekday() == 5 and local_time < SATURDAY_OPEN:
                return "hoje (sábado) às 09:00 (horário de Campo Grande/MS)"

        # Otherwise scan forward up to 10 days for the next open business morning
        for offset in range(1, 11):
            candidate = today + timedelta(days=offset)
            if candidate.weekday() == 6:
                continue
            if self.get_holiday_name(candidate) is not None:
                continue
            day_label = WEEKDAY_NAMES_PT_BR[candidate.weekday()].lower()
            date_label = candidate.strftime("%d/%m")
            if offset == 1:
                return f"amanhã ({day_label}, {date_label}) às 09:00 (horário de Campo Grande/MS)"
            return f"{day_label} ({date_label}) às 09:00 (horário de Campo Grande/MS)"

        return "no próximo dia útil às 09:00 (horário de Campo Grande/MS)"
