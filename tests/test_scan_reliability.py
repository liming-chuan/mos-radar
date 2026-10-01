import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
from test_short_term import bars, NOW, next_bars
from test_entry_guards import guarded
from price_feed import fetch_bars, issue, PriceFrames
from short_term import advance_trade, report_html
from short_term_scan import run
from brief_delivery import deliver


class FeedTests(unittest.TestCase):
    def test_bad_batch_is_retried_without_filling_prices(self):
        stock, base = bars()
        bad = stock.copy()
        bad.loc[bad.index[-2], 'High'] = float('nan')
        calls = []
        def download(tickers, **kwargs):
            calls.append(tickers)
            if len(tickers) == 1:
                return base if tickers == ['^GSPC'] else stock
            result = pd.concat({'A':bad, 'B':stock}, axis=1)
            return result.swaplevel(0,1,axis=1)
        with patch('price_feed.yf.download',side_effect=download):
            frames = fetch_bars(['^GSPC','A','B'],'us',NOW)
        self.assertEqual(calls,[['^GSPC'],['A','B'],['A']])
        self.assertEqual(frames.diagnostics['A']['reason'],'')
        self.assertIn('High',frames.diagnostics['A']['first_issue'])
        self.assertEqual(frames['A'].loc[stock.index[-2],'High'],stock.loc[stock.index[-2],'High'])

    def test_total_failure_has_bounded_retries_and_continues_batches(self):
        tickers=['^GSPC']+[str(i) for i in range(61)]
        calls=[]
        def broken(tickers,**kwargs):
            calls.append(tickers)
            raise RuntimeError('429 vendor test')
        with patch('price_feed.yf.download',side_effect=broken):
            frames=fetch_bars(tickers,'us',NOW)
        self.assertEqual(len(calls),2+4+40)
        self.assertEqual(set(frames.diagnostics),set(tickers))
        self.assertTrue(all(d['reason'] for d in frames.diagnostics.values()))
        self.assertIn('429',frames.diagnostics['60']['vendor_error'])

    def test_active_positions_are_fetched_separately_before_rotation(self):
        stock,_=bars();calls=[]
        with patch('price_feed.yf.download',side_effect=lambda ts,**kw:(calls.append(ts) or stock)):
            fetch_bars(['^GSPC','ACTIVE','A'],'us',NOW,priority=['ACTIVE'])
        self.assertEqual(calls,[['^GSPC'],['ACTIVE'],['A']])

    def test_validation_excludes_unfinished_future_bar_but_requires_continuity(self):
        stock,_=bars()
        extra=stock.tail(1).copy();extra.index=pd.to_datetime(['2026-09-14']);extra['High']=float('nan')
        self.assertEqual(issue(pd.concat([stock,extra]),'us',NOW),'')
        self.assertIn('不齐',issue(stock.drop(stock.index[-4]),'us',NOW))
        stock.loc[stock.index[-2],'Adj Close']=float('nan')
        self.assertIn('Adj Close',issue(stock,'us',NOW))


class RecoveryTests(unittest.TestCase):
    def test_failed_scan_retains_last_good_and_does_not_advance_rotation(self):
        stock,base=bars()
        row=dict(ticker='TEST',quote_type='EQUITY',scan_time=NOW.isoformat(),liquidity_value=100e6,
                 equity=100,statement_evidence_status='NOT_CONFIGURED')
        events=lambda ts,now:{t:dict(dates=['2026-11-01'],source='test',observed_at=now.isoformat()) for t in ts}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            rows=[row]+[dict(row,ticker=f'T{i}',liquidity_value=50e6) for i in range(400)]
            pd.DataFrame(rows).to_csv(root/'mos_market_latest.csv',index=False)
            good=run('us',root,NOW,fetch=lambda *a:{'TEST':stock,'^GSPC':base},event_fetch=events)
            saved=(root/'mos_short_last_good.json').read_bytes()
            bad=run('us',root,NOW,fetch=lambda *a:{},event_fetch=events)
            self.assertEqual(bad['health']['status'],'FAILED')
            self.assertNotEqual(good['coverage']['next_cursor'],0)
            self.assertEqual(bad['coverage']['next_cursor'],good['coverage']['next_cursor'])
            self.assertEqual((root/'mos_short_last_good.json').read_bytes(),saved)
            self.assertEqual(len(bad['trades']),len(good['trades']))
            self.assertIn('扫描故障',report_html('us',root,NOW))
            self.assertNotIn('最高追价',report_html('us',root,NOW))

    def test_raw_failure_evidence_is_saved_without_filling_bad_bar(self):
        stock,base=bars();stock.loc[stock.index[-2],'High']=float('nan')
        frames=PriceFrames();frames.update({'TEST':stock,'^GSPC':base})
        frames.diagnostics={'TEST':dict(reason=issue(stock,'us',NOW),attempts=2)}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=dict(ticker='TEST',quote_type='EQUITY',scan_time=NOW.isoformat(),liquidity_value=100e6,
                     equity=100,statement_evidence_status='NOT_CONFIGURED')
            pd.DataFrame([row]).to_csv(root/'mos_market_latest.csv',index=False)
            run('us',root,NOW,fetch=lambda *a:frames,event_fetch=lambda *a:{})
            raw=pd.read_csv(root/'mos_short_bad_bars.csv')
            self.assertEqual(len(raw),60)
            self.assertEqual(set(raw.ticker),{'TEST'})
            self.assertTrue(pd.isna(raw.loc[raw.date==str(stock.index[-2].date()),'High']).all())

    def test_missing_holding_bar_recovers_continuously_once(self):
        p=dict(guarded(),state='OPEN',fill=100.,stop=95.,initial_stop=95.,initial_risk=5.,
               target=120.,held_sessions=1,last_processed='2026-09-14',filled_on='2026-09-14')
        suspended=advance_trade(p,None,'us','2026-09-16T22:00Z')
        self.assertEqual(suspended['state'],'DATA_REVIEW')
        self.assertEqual(suspended['last_processed'],'2026-09-14')
        frame=pd.concat([next_bars(102,104,101,103)]*2)
        frame.index=pd.to_datetime(['2026-09-15','2026-09-16'])
        recovered=advance_trade(suspended,frame,'us','2026-09-16T22:00Z')
        self.assertEqual(recovered['state'],'OPEN')
        self.assertEqual(recovered['held_sessions'],3)
        self.assertEqual(recovered['last_processed'],'2026-09-16')
        self.assertEqual(advance_trade(recovered,frame,'us','2026-09-16T22:00Z'),recovered)


class DeliveryTests(unittest.TestCase):
    def snapshot(self,root,health='OK'):
        root.mkdir()
        (root/'mos_short_term.json').write_text(json.dumps(dict(policy='breakout-1',market='us',signal_date='2026-09-11',
            observed_at=NOW.isoformat(),health=dict(status=health),coverage={},plans=[],trades=[])),encoding='utf-8')

    def test_after_close_and_premarket_send_only_once_for_next_session(self):
        sent=[]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'state';self.snapshot(root)
            first=deliver('us',root,'after-close',NOW,sender=lambda *a:sent.append(a))
            second=deliver('us',root,'premarket','2026-09-14T12:00Z',sender=lambda *a:sent.append(a))
            self.assertEqual(first,dict(status='SENT',session='2026-09-14'))
            self.assertEqual(second['status'],'ALREADY_SENT')
            self.assertEqual(len(sent),1)
            self.assertIn('盘后提前准备',sent[0][0])

    def test_failed_snapshot_defers_early_email_but_sends_failure_once_in_morning(self):
        sent=[]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'state';self.snapshot(root,'FAILED')
            self.assertEqual(deliver('us',root,'after-close',NOW,sender=lambda *a:sent.append(a))['status'],'DEFERRED_DATA')
            self.assertFalse((root/'mos_brief_delivery.json').exists())
            result=deliver('us',root,'premarket','2026-09-14T12:00Z',sender=lambda *a:sent.append(a))
            self.assertEqual(result['status'],'SENT')
            self.assertIn('扫描故障',sent[0][1])

    def test_dry_run_and_smtp_failure_never_mark_sent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'state';self.snapshot(root)
            result=deliver('us',root,'after-close',NOW,dry_run=True)
            self.assertEqual(result['status'],'DRY_RUN')
            self.assertFalse((root/'mos_brief_delivery.json').exists())
            with self.assertRaises(RuntimeError):
                deliver('us',root,'after-close',NOW,sender=lambda *a:(_ for _ in ()).throw(RuntimeError('SMTP test')))
            self.assertFalse((root/'mos_brief_delivery.json').exists())

    def test_holidays_and_expired_sessions_are_not_sent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'state'
            self.assertEqual(deliver('us',root,now='2026-09-07T12:00Z')['status'],'SKIPPED_HOLIDAY')
            self.assertEqual(deliver('us',root,now='2026-09-14T21:00Z')['status'],'SKIPPED_ENDED')
            self.assertEqual(deliver('us',root,'after-close','2026-09-14T15:00Z')['status'],'DEFERRED_LATE')
            self.assertFalse(root.exists())

    def test_corrupt_receipt_blocks_sending_before_smtp(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'state';self.snapshot(root)
            (root/'mos_brief_delivery.json').write_text(json.dumps(dict(market='us',sessions=['bad'])))
            with self.assertRaises(ValueError), patch('emailer.send_email') as sender:
                deliver('us',root,'after-close',NOW)
            sender.assert_not_called()


if __name__=='__main__':
    unittest.main()
