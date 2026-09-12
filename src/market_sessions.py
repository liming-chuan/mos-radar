"""Exchange sessions, including holidays, half days and a final-bar buffer."""
from functools import lru_cache
import pandas as pd
import exchange_calendars as xc


@lru_cache(maxsize=2)
def calendar(market):
    if market not in {'hk', 'us'}:
        raise ValueError('unsupported market')
    return xc.get_calendar('XHKG' if market == 'hk' else 'XNYS')


def timestamp(now=None):
    value = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if value.tzinfo is None:
        raise ValueError('time must include timezone')
    return value.tz_convert('UTC')


def latest_completed(market, now=None):
    now = timestamp(now)
    cal = calendar(market)
    local = now.tz_convert('Asia/Hong_Kong' if market == 'hk' else 'America/New_York')
    session = cal.date_to_session(str(local.date()), direction='previous')
    # HK auction can run beyond 16:00; also allow daily-bar publication lag.
    if now < cal.session_close(session) + pd.Timedelta(minutes=20):
        session = cal.previous_session(session)
    return session
