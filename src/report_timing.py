"""Label scheduled reports by actual market-local production time."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from market_sessions import calendar

HK_TZ = timezone(timedelta(hours=8))


def hk_time(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError('report time must include timezone')
    return value.astimezone(HK_TZ)


def premarket_title(mode, market, now=None):
    if market == 'hk' and mode == 'premarket_scan':
        local = hk_time(now)
        if not calendar(market).is_session(str(local.date())):
            return '休市日安全边际扫描'
        if (local.hour, local.minute) >= (9, 30):
            return '延迟安全边际扫描（原定盘前）'
    if market == 'us' and mode == 'premarket_scan':
        local = market_time(market, now)
        if not calendar(market).is_session(str(local.date())):
            return '休市日安全边际扫描'
        if (local.hour, local.minute) >= (9, 30):
            return '延迟安全边际扫描（原定盘前）'
    return None


def market_time(market, now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError('report time must include timezone')
    return value.astimezone(ZoneInfo('Asia/Hong_Kong' if market == 'hk' else 'America/New_York'))
