"""SBC availability describes evidence, never assumes zero compensation."""
import pandas as pd

SBC_LABELS = {'STATEMENT_UNAVAILABLE': '现金流表不可用', 'LINE_MISSING': '未返回SBC项目',
              'VALUES_MISSING': 'SBC项目有列但无数值', 'PARTIAL_HISTORY': 'SBC部分年份缺失',
              'INSUFFICIENT_HISTORY': 'SBC覆盖不足三年', 'AVAILABLE': 'SBC最近可用年度有值',
              'NOT_FETCHED': '尚未请求现金流表'}


def sbc_coverage(frame):
    if frame is None or frame.empty:
        return 'STATEMENT_UNAVAILABLE', ''
    columns = frame.dropna(axis=1, how='all').columns.sort_values(ascending=False)[:5]
    names = {str(x).lower(): x for x in frame.index}
    field = names.get('stock based compensation')
    if field is None:
        return 'LINE_MISSING', ';'.join(str(pd.Timestamp(x).date()) for x in columns)
    values = pd.to_numeric(frame.loc[field].reindex(columns), errors='coerce').replace([float('inf'), -float('inf')], float('nan'))
    missing = ';'.join(str(pd.Timestamp(x).date()) for x in columns[values.isna()])
    if not values.notna().any():
        return 'VALUES_MISSING', missing
    if values.isna().any():
        return 'PARTIAL_HISTORY', missing
    return ('INSUFFICIENT_HISTORY' if len(values) < 3 else 'AVAILABLE'), missing
