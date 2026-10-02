"""Operational evidence completeness; never a trading-strategy threshold."""
MIN_VALID_RATIO = .80


def assess(selected, failed, regime, tracking_gaps=()):
    valid=max(0, selected-failed)
    ratio=valid/selected if selected else 0.
    reasons=[]
    if not selected:
        reasons.append('无合格股票可检查')
    elif ratio<MIN_VALID_RATIO:
        reasons.append(f'有效覆盖{valid}/{selected}（{ratio:.1%}），低于80%运行门槛')
    if regime.get('status') not in {'ALLOW','BLOCK'}:
        reasons.append('基准证据未就绪')
    if tracking_gaps:
        reasons.append('既有模拟记录缺少所需行情：'+','.join(sorted(set(tracking_gaps))))
    return dict(status='FAILED' if reasons else ('PARTIAL' if failed else 'OK'),
                valid=valid,valid_ratio=ratio,min_valid_ratio=MIN_VALID_RATIO,
                tracking_gaps=sorted(set(tracking_gaps)),
                reason='；'.join(reasons) if reasons else ('部分股票证据不足，已排除' if failed else '行情校验通过'))


def snapshot_health(data):
    """Re-evaluate legacy snapshots instead of trusting an old green label."""
    try:
        coverage=data['coverage']
        selected=int(coverage['selected']);failed=int(coverage['data_failed'])
        if selected<=0 or not 0<=failed<=selected:
            raise ValueError('invalid coverage')
        plans=data['plans']
        names={p['ticker'] for p in plans}
        errors={p['ticker'] for p in plans if p['status']=='DATA'}
        if len(names)!=selected or len(errors)!=failed:
            raise ValueError('coverage disagrees with plans')
        health=assess(selected,failed,data.get('market_gate',{}),data.get('health',{}).get('tracking_gaps',[]))
        if data.get('health',{}).get('status')=='FAILED':
            health.update(status='FAILED',reason=data['health'].get('reason',health['reason']))
        return health
    except (KeyError,TypeError,ValueError):
        return dict(status='FAILED',reason='快照覆盖证据缺失或不一致',valid_ratio=0.)


def usable(data):
    return snapshot_health(data)['status'] in {'OK','PARTIAL'}
