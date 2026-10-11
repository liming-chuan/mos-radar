"""Atomic resumable work; only a complete scan may replace the public snapshot."""
import hashlib
import json
from datetime import datetime, timezone


def scan_identity(tickers, market, model):
    from statement_evidence import evidence_fingerprint
    scope = dict(tickers=tickers, market=market, model=model,
                 evidence=evidence_fingerprint() if market == 'hk' else None, schema=1)
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def load_checkpoint(path, identity, tickers, max_age_days, now=None):
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        age = ((now or datetime.now(timezone.utc))-datetime.fromisoformat(payload['started_at'])).total_seconds()/86400
        rows = payload['rows']
        if payload['identity'] != identity or not 0 <= age <= max_age_days or not isinstance(rows, list):
            return []
        if len(rows) > len(tickers) or [r['ticker'] for r in rows] != tickers[:len(rows)]:
            return []
        if any(float(r.get('cache_age_days') or 0) + age > max_age_days for r in rows):
            return []
        return rows
    except (OSError, ValueError, TypeError, KeyError):
        return []


def save_checkpoint(path, identity, rows):
    # Preserve the initial timestamp so repeatedly interrupted work still expires.
    started = datetime.now(timezone.utc).isoformat()
    try:
        old = json.loads(path.read_text(encoding='utf-8'))
        if old.get('identity') == identity:
            started = old['started_at']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(dict(identity=identity, started_at=started, rows=rows), ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)
