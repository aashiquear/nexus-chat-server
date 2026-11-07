"""Date/time arithmetic and currency conversion."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from zoneinfo import ZoneInfo, available_timezones

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)


def _parse_date(text: str, tz: str | None = None) -> dt.datetime:
    text = (text or "").strip()
    now = dt.datetime.now(ZoneInfo(tz) if tz else None)
    low = text.lower()
    if low in ("", "now", "today"):
        return now if low == "now" else now.replace(hour=0, minute=0, second=0, microsecond=0)
    if low == "tomorrow":
        return (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if low == "yesterday":
        return (now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            d = dt.datetime.strptime(text, fmt)
            if d.tzinfo is None and tz:
                d = d.replace(tzinfo=ZoneInfo(tz))
            return d
        except ValueError:
            continue
    try:
        d = dt.datetime.fromisoformat(text)
        if d.tzinfo is None and tz:
            d = d.replace(tzinfo=ZoneInfo(tz))
        return d
    except ValueError:
        raise ValueError(f"Unrecognised date '{text}'. Use ISO format like 2026-03-14 or 2026-03-14T09:30.")


_PERIOD_RE = re.compile(r"([+-]?\d+)\s*(years?|y|months?|mo|weeks?|w|days?|d|hours?|h|minutes?|min|m|seconds?|s)\b", re.I)


def _add_period(d: dt.datetime, period: str, sign: int = 1) -> dt.datetime:
    months = 0
    delta = dt.timedelta()
    for num, unit in _PERIOD_RE.findall(period or ""):
        n = int(num) * sign
        u = unit.lower()
        if u.startswith("y"):
            months += 12 * n
        elif u.startswith("mo") or u == "months" or u == "month":
            months += n
        elif u in ("m", "min") or u.startswith("minute"):
            delta += dt.timedelta(minutes=n)
        elif u.startswith("w"):
            delta += dt.timedelta(weeks=n)
        elif u.startswith("d"):
            delta += dt.timedelta(days=n)
        elif u.startswith("h"):
            delta += dt.timedelta(hours=n)
        elif u.startswith("s"):
            delta += dt.timedelta(seconds=n)
    if months:
        total = d.month - 1 + months
        year = d.year + total // 12
        month = total % 12 + 1
        import calendar

        day = min(d.day, calendar.monthrange(year, month)[1])
        d = d.replace(year=year, month=month, day=day)
    return d + delta


def _holiday_set(country: str | None, years: list[int], subdiv: str | None = None):
    if not country:
        return {}
    try:
        import holidays as hol

        return hol.country_holidays(country.upper(), subdiv=subdiv, years=years)
    except Exception as e:
        logger.debug("holidays unavailable: %s", e)
        return {}


@register_tool("datetime_calculate")
class DateTimeCalculateTool(BaseTool):
    name = "datetime_calculate"
    description = (
        "Date and time arithmetic. Operations: difference (between two dates: days, weeks, months, business days, "
        "human-readable), add (date + period like '3 weeks 2 days' or '-18 months'), business_days (count or add "
        "working days, skipping weekends and optional public holidays by country), weekday, week_number, "
        "convert_timezone, holidays (list public holidays for a country/year), age. Dates in ISO format "
        "(2026-03-14, 2026-03-14T09:30); 'today', 'now', 'tomorrow' also work."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["difference", "add", "business_days", "weekday", "week_number", "convert_timezone", "holidays", "age"]},
            "date": {"type": "string", "description": "Primary date/time (default today)."},
            "date2": {"type": "string", "description": "Second date (difference, business_days count)."},
            "period": {"type": "string", "description": "Period for add, e.g. '2 weeks', '90 days', '-1 month 3 days', '36 hours'."},
            "days": {"type": "integer", "description": "Business days to add (business_days with a single date)."},
            "country": {"type": "string", "description": "ISO country code for public holidays (US, GB, DE, IN, …)."},
            "subdivision": {"type": "string", "description": "State/province code for regional holidays (e.g. CA, BY)."},
            "timezone": {"type": "string", "description": "IANA timezone of the input (e.g. Europe/Berlin)."},
            "to_timezone": {"type": "string", "description": "Target timezone for convert_timezone."},
            "year": {"type": "integer", "description": "Year for holidays listing."},
        },
        "required": ["operation"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            return json.dumps(self._run(kwargs), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _run(self, k: dict) -> dict:
        op = k["operation"]
        tz = k.get("timezone")
        if tz and tz not in available_timezones():
            raise ValueError(f"unknown timezone {tz}")
        d1 = _parse_date(k.get("date") or "today", tz)
        out: dict = {"operation": op, "date": d1.isoformat()}

        if op == "difference" or op == "age":
            d2 = _parse_date(k.get("date2") or "today", tz) if op == "difference" else dt.datetime.now(d1.tzinfo)
            if op == "age":
                d1, d2 = min(d1, d2), max(d1, d2)
            delta = d2 - d1
            days = delta.days
            # calendar months/years
            y = d2.year - d1.year
            m = d2.month - d1.month
            dd = d2.day - d1.day
            if dd < 0:
                m -= 1
            if m < 0:
                y -= 1
                m += 12
            bdays = sum(1 for i in range(abs(days)) if (min(d1, d2) + dt.timedelta(days=i)).weekday() < 5)
            hols = _holiday_set(k.get("country"), list(range(min(d1, d2).year, max(d1, d2).year + 1)), k.get("subdivision"))
            if hols:
                bdays -= sum(1 for i in range(abs(days)) if (lambda x: x.weekday() < 5 and x.date() in hols)(min(d1, d2) + dt.timedelta(days=i)))
            out.update({
                "date2": d2.isoformat(), "total_days": days, "total_seconds": delta.total_seconds(), "total_hours": delta.total_seconds() / 3600,
                "weeks": days / 7, "calendar": {"years": y, "months": m, "days": abs(dd) if dd >= 0 else (dt.date(d2.year, d2.month, 1) - dt.timedelta(days=1)).day + dd},
                "business_days": bdays, "human": f"{y} year(s), {m} month(s)" if y or m else f"{abs(days)} day(s)",
            })
        elif op == "add":
            res = _add_period(d1, k.get("period") or "")
            out.update({"period": k.get("period"), "result": res.isoformat(), "weekday": res.strftime("%A")})
        elif op == "business_days":
            hols = _holiday_set(k.get("country"), [d1.year, d1.year + 1, d1.year - 1], k.get("subdivision"))
            if k.get("date2"):
                d2 = _parse_date(k["date2"], tz)
                step = 1 if d2 >= d1 else -1
                cur, count = d1, 0
                while (cur < d2) if step > 0 else (cur > d2):
                    if cur.weekday() < 5 and cur.date() not in hols:
                        count += 1
                    cur += dt.timedelta(days=step)
                out.update({"date2": d2.isoformat(), "business_days": count * step})
            else:
                n = int(k.get("days") or 0)
                step = 1 if n >= 0 else -1
                cur, remaining = d1, abs(n)
                while remaining > 0:
                    cur += dt.timedelta(days=step)
                    if cur.weekday() < 5 and cur.date() not in hols:
                        remaining -= 1
                out.update({"days_added": n, "result": cur.isoformat(), "weekday": cur.strftime("%A")})
            if hols:
                out["holidays_considered"] = k.get("country")
        elif op == "weekday":
            out.update({"weekday": d1.strftime("%A"), "day_of_year": d1.timetuple().tm_yday, "is_weekend": d1.weekday() >= 5})
        elif op == "week_number":
            iso = d1.isocalendar()
            out.update({"iso_week": iso[1], "iso_year": iso[0], "weekday": d1.strftime("%A")})
        elif op == "convert_timezone":
            target = k.get("to_timezone")
            if not target or target not in available_timezones():
                raise ValueError("to_timezone must be a valid IANA zone like America/New_York")
            if d1.tzinfo is None:
                d1 = d1.replace(tzinfo=ZoneInfo(tz or "UTC"))
            res = d1.astimezone(ZoneInfo(target))
            out.update({"from": d1.isoformat(), "to": res.isoformat(), "to_timezone": target, "offset_hours": res.utcoffset().total_seconds() / 3600})
        elif op == "holidays":
            year = int(k.get("year") or d1.year)
            hols = _holiday_set(k.get("country"), [year], k.get("subdivision"))
            if not k.get("country"):
                raise ValueError("country is required")
            out.update({"country": k["country"], "year": year, "holidays": [{"date": str(d), "name": n} for d, n in sorted(hols.items())]})
        return out


@register_tool("currency_convert")
class CurrencyConvertTool(BaseTool):
    name = "currency_convert"
    description = (
        "Convert an amount between currencies using European Central Bank reference rates (daily, via "
        "frankfurter.app; no API key). Supports historical dates. Returns the rate and its date so you can cite it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "Amount to convert (default 1)."},
            "from_currency": {"type": "string", "description": "ISO code, e.g. USD."},
            "to_currency": {"type": "string", "description": "ISO code or comma-separated list, e.g. EUR or EUR,GBP,JPY."},
            "date": {"type": "string", "description": "Optional historical date YYYY-MM-DD (default latest)."},
        },
        "required": ["from_currency", "to_currency"],
    }

    async def execute(self, **kwargs) -> str:
        import httpx

        amount = float(kwargs.get("amount") or 1)
        src = (kwargs.get("from_currency") or "").upper().strip()
        dst = (kwargs.get("to_currency") or "").upper().replace(" ", "")
        date = kwargs.get("date") or "latest"
        base = self.config.get("api_url", "https://api.frankfurter.app")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{base}/{date}", params={"from": src, "to": dst})
                if r.status_code != 200:
                    return json.dumps({"error": f"rate service HTTP {r.status_code}: {r.text[:200]}"})
                data = r.json()
        except Exception as e:
            return json.dumps({"error": f"rate lookup failed: {e}"})
        rates = data.get("rates", {})
        return json.dumps({
            "amount": amount, "from": src, "date": data.get("date"),
            "results": {cur: round(amount * rate, 4) for cur, rate in rates.items()},
            "rates": rates, "source": "European Central Bank reference rates via frankfurter.app",
        })
