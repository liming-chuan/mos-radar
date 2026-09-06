"""Reviewed annual filing supplements. Never infer values or overwrite the provider."""
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import json
import math

import pandas as pd

PATH = Path(__file__).resolve().parents[1] / 'data/hk_statement_evidence.json'
FIELDS = {
    'revenue': ('income', 'Total Revenue'),
    'net_income': ('income', 'Net Income'),
    'operating_income': ('income', 'Operating Income'),
    'gross_profit': ('income', 'Gross Profit'),
    'ebitda': ('income', 'EBITDA'),
    'interest_expense': ('income', 'Interest Expense'),
    'operating_cash_flow': ('cashflow', 'Operating Cash Flow'),
    'capital_expenditure': ('cashflow', 'Capital Expenditure'),
    'sbc': ('cashflow', 'Stock Based Compensation'),
}
REQUIRED = {'revenue', 'net_income', 'operating_income', 'operating_cash_flow', 'capital_expenditure'}
ALIASES = {'Total Revenue': ['Operating Revenue'], 'Net Income': ['Net Income Common Stockholders'],
           'Operating Income': ['Operating Income or Loss'], 'Operating Cash Flow': ['Total Cash From Operating Activities'],
           'Capital Expenditure': ['Capital Expenditures'], 'Interest Expense': ['Interest Expense Non Operating']}


def evidence_fingerprint():
    return hashlib.sha256(PATH.read_bytes()).hexdigest() if PATH.exists() else 'NONE'


def supplement_annual(ticker, currency, income, cashflow, now=None, records=None):
    """All amounts in original currency units, consolidated annual statements only.

    Metadata is a review record, not automatic proof of a filing's authenticity.
    One rejected record rejects the ticker's entire supplement, including conflicts.
    """
    original = (income, cashflow)
    audit = {'status': 'NONE', 'sources': [], 'filled': [], 'errors': []}
    try:
        if records is None:
            records = json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else []
        if not isinstance(records, list):
            raise ValueError('evidence file must contain a list')
        if any(not isinstance(r, dict) or not r.get('ticker') for r in records):
            raise ValueError('each evidence record needs a ticker')
        selected = [r for r in records if r.get('ticker') == ticker]
        if not selected:
            return *original, audit
        now = pd.Timestamp.now(tz='UTC') if now is None else pd.to_datetime(now, utc=True)
        frames = {'income': income.copy() if income is not None else pd.DataFrame(),
                  'cashflow': cashflow.copy() if cashflow is not None else pd.DataFrame()}
        for frame in frames.values():
            frame.columns = pd.to_datetime(frame.columns, utc=True).tz_localize(None)
            if frame.columns.has_duplicates or frame.index.has_duplicates:
                raise ValueError('duplicate provider rows or periods')
        seen = set()
        for r in selected:
            end, start, published, reviewed = [pd.to_datetime(r.get(k), utc=True, errors='coerce') for k in
                                             ('period_end', 'period_start', 'published_at', 'reviewed_at')]
            if any(pd.isna(x) for x in (end, start, published, reviewed)):
                raise ValueError('missing or invalid dates')
            if not 330 <= (end-start).days <= 380 or not end <= published <= reviewed <= now:
                raise ValueError('not an annual period or publication/review dates invalid')
            if any(abs((end-previous).days) < 300 for previous in seen):
                raise ValueError('duplicate or overlapping annual evidence periods')
            seen.add(end)
            url = urlparse(str(r.get('source_url', '')))
            if url.scheme != 'https' or url.hostname not in {'www.hkexnews.hk', 'www1.hkexnews.hk'} or not url.path.endswith('.pdf') or url.username or url.password:
                raise ValueError('source must be a public HKEXnews PDF')
            if r.get('currency') != currency or not currency:
                raise ValueError('financial currency mismatch')
            if r.get('verified') is not True or not str(r.get('reviewed_by', '')).strip() or not str(r.get('source_pages', '')).strip():
                raise ValueError('missing review or page references')
            if r.get('scope') != 'consolidated' or type(r.get('unit')) is not int or r.get('unit') != 1:
                raise ValueError('require consolidated statements in currency units')
            values = r.get('values', {})
            if not isinstance(values, dict) or not REQUIRED.issubset(values) or set(values)-set(FIELDS):
                raise ValueError('missing required fields or unknown fields')
            date = end.tz_localize(None)
            for key, value in values.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('non-finite or non-numeric amount')
                name, field = FIELDS[key]
                frame = frames[name]
                lookup = {str(x).lower(): x for x in frame.index}
                field = next((lookup[x.lower()] for x in [field]+ALIASES.get(field, []) if x.lower() in lookup), field)
                # CapEx and SBC are deductions regardless of the statement's sign.
                value = abs(value) if key in {'capital_expenditure', 'sbc'} else value
                old = frame.at[field, date] if field in frame.index and date in frame.columns else None
                if old is not None and pd.notna(old):
                    old = abs(float(old)) if key in {'capital_expenditure', 'sbc'} else float(old)
                    if not math.isclose(old, value, rel_tol=1e-6, abs_tol=1):
                        raise ValueError(f'provider conflict: {key} {date.date()}')
                else:
                    frame.at[field, date] = value
                    audit['filled'].append(f'{key}@{date.date()}')
            source = {k: r[k] for k in ('period_end', 'published_at', 'source_url', 'source_pages', 'reviewed_by', 'reviewed_at')}
            source['sbc_provided'] = 'sbc' in values
            source['notes'] = str(r.get('notes', ''))
            audit['sources'].append(source)
        audit['status'] = 'SUPPLEMENTED' if audit['filled'] else 'MATCHED'
        return frames['income'].sort_index(axis=1, ascending=False), frames['cashflow'].sort_index(axis=1, ascending=False), audit
    except (ValueError, TypeError, KeyError, OSError) as exc:
        audit.update(status='REJECTED', filled=[], sources=[], errors=[str(exc)])
        return *original, audit
