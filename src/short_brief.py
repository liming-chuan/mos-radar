"""Prepare the existing daily email before expensive fundamental work."""
from html import escape
from market_sessions import timestamp
from report_timing import market_time, premarket_title
from short_term import report_html


def prepare_brief(market, state_dir=None, now=None):
    now = timestamp(now)
    local = market_time(market, now)
    label = '港股' if market == 'hk' else '美股'
    timing = premarket_title('premarket_scan', market, now)
    suffix = ' · 延迟生成' if timing and '延迟' in timing else (' · 休市日' if timing else '')
    subject = f'【MOS Radar {label}】{local:%Y-%m-%d} 短线机会简报{suffix}'
    body = '<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{color:#172b3a;overflow-wrap:anywhere}h1{font-size:23px}h2{font-size:21px}h3{font-size:17px}.stock{border:1px solid #dbe3ec;border-left:4px solid #187b80;border-radius:6px;padding:14px;margin:18px 0;background:#f7fbfb}.sub{color:#526271;font-size:14px}</style></head><body style="max-width:760px;margin:20px auto;padding:16px;font:16px/1.7 sans-serif">'
    body += f'<h1>{escape(subject)}</h1><p>简报生成 {local:%Y-%m-%d %H:%M}（市场当地时间）。使用最近盘后短线快照，未等待本轮全量财报扫描；不提供实时触发确认。</p>'
    body += report_html(market, state_dir, now)
    body += '<p>完整价值扫描将随后继续，结果保存在本次日扫描任务产物中。目标价是规则情景，不是收益预测。</p></body></html>'
    return subject, body
