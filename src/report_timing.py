"""Label HK scheduled reports by their actual production time, not job name."""
from datetime import datetime, timezone, timedelta

HK_TZ = timezone(timedelta(hours=8))


def hk_time(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError('report time must include timezone')
    return value.astimezone(HK_TZ)


def premarket_title(mode, market, now=None):
    if market == 'hk' and mode == 'premarket_scan':
        local = hk_time(now)
        if (local.hour, local.minute) >= (9, 30):
            return '延迟安全边际扫描（原定盘前）'
    return None
