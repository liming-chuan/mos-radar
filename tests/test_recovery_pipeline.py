import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import pandas as pd
from test_short_term import bars, NOW, next_bars
from test_entry_guards import guarded
from scan_health import assess, snapshot_health
from short_pipeline import refresh, publish_result
from short_term_scan import run, choose_batch
from short_term import report_html, advance_trade
from price_feed import fetch_bars, pending_daily_bar
from brief_delivery import deliver
from persist_public_state import persist


def snapshot(root,selected=300,failed=0,status='PARTIAL'):
    plans=[dict(ticker=str(i),status='DATA' if i<failed else 'WAIT') for i in range(selected)]
    data=dict(policy='breakout-1',market='us',observed_at=NOW.isoformat(),signal_date='2026-09-11',
              health=dict(status=status),market_gate=dict(status='ALLOW'),
              coverage=dict(selected=selected,data_failed=failed,next_cursor=300),plans=plans,trades=[])
    (root/'mos_short_term.json').write_text(json.dumps(data),encoding='utf-8')
    return data


class CompletenessTests(unittest.TestCase):
    def test_pending_guard_unknown_is_reversible_only_before_entry(self):
        stock,_=bars()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=dict(ticker='TEST',quote_type='EQUITY',scan_time=NOW.isoformat(),liquidity_value=100e6,
                     equity=100,statement_evidence_status='NOT_CONFIGURED')
            pd.DataFrame([row]).to_csv(root/'mos_market_latest.csv',index=False)
            original=snapshot(root,selected=1);trade=dict(guarded(),state='PENDING')
            original['trades']=[trade];(root/'mos_short_term.json').write_text(json.dumps(original))
            frames=lambda *a:{'TEST':stock,'^GSPC':stock}
            unknown=run('us',root,NOW,fetch=frames,event_fetch=lambda *a:{})
            self.assertEqual(unknown['trades'][0]['state'],'DATA_REVIEW')
            self.assertEqual(unknown['trades'][0]['earnings_gate']['status'],'UNKNOWN')
            events=lambda ts,now:{t:dict(dates=['2026-11-01'],source='test',observed_at=now.isoformat()) for t in ts}
            recovered=run('us',root,NOW+pd.Timedelta(hours=1),fetch=frames,event_fetch=events)
            self.assertEqual(recovered['trades'][0]['state'],'PENDING')
            self.assertNotIn('data_reason',recovered['trades'][0])
            # Recovery after entry day cannot invent an earlier approval.
            trade=unknown['trades'][0]
            late=advance_trade(trade,next_bars(103,104,102,103),'us','2026-09-14T22:00Z')
            self.assertEqual(late['state'],'CANCELLED_GUARD')

    def test_coverage_boundary_and_blocked_market_are_healthy(self):
        self.assertEqual(assess(300,60,{'status':'BLOCK'})['status'],'PARTIAL')
        self.assertEqual(assess(300,61,{'status':'ALLOW'})['status'],'FAILED')
        self.assertEqual(assess(300,0,{'status':'UNKNOWN'})['status'],'FAILED')
        self.assertEqual(assess(300,0,{'status':'ALLOW'},['HELD'])['status'],'FAILED')

    def test_legacy_298_of_300_invalid_cannot_mail_or_show_prices(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=snapshot(root,failed=298)
            self.assertEqual(snapshot_health(data)['status'],'FAILED')
            self.assertIn('扫描故障',report_html('us',root,NOW))
            sender=Mock()
            self.assertEqual(deliver('us',root,'after-close',NOW,sender=sender)['status'],'DEFERRED_DATA')
            self.assertEqual(deliver('us',root,'premarket','2026-09-14T12:00Z',sender=sender)['status'],'DEFERRED_DATA')
            sender.assert_not_called()
            self.assertFalse((root/'mos_brief_delivery.json').exists())
            selected,cursor=choose_batch([str(i) for i in range(700)],data)
            self.assertEqual(selected,[str(i) for i in range(300)])
            self.assertEqual(cursor,300)

    def test_partial_failure_freezes_cohort_until_valid_recovery(self):
        stock,base=bars()
        events=lambda ts,now:{t:dict(dates=['2026-11-01'],source='test',observed_at=now.isoformat()) for t in ts}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            rows=[dict(ticker=str(i),quote_type='EQUITY',scan_time=NOW.isoformat(),liquidity_value=100e6-i,
                       equity=100,statement_evidence_status='NOT_CONFIGURED') for i in range(700)]
            pd.DataFrame(rows).to_csv(root/'mos_market_latest.csv',index=False)
            # First healthy rotation makes a real last-good snapshot.
            good=run('us',root,NOW,fetch=lambda ts,*a:{t:(base if t=='^GSPC' else stock) for t in ts},event_fetch=events)
            backup=(root/'mos_short_last_good.json').read_bytes()
            bad=run('us',root,NOW,fetch=lambda ts,*a:{t:base if t=='^GSPC' else stock for t in ts[:3]},event_fetch=events)
            self.assertEqual(bad['health']['status'],'FAILED')
            self.assertEqual((root/'mos_short_last_good.json').read_bytes(),backup)
            self.assertEqual(bad['coverage']['next_cursor'],good['coverage']['next_cursor'])
            self.assertFalse(any(p['status']=='PENDING' for p in bad['plans']))
            cohort={p['ticker'] for p in bad['plans']}
            recovered=run('us',root,NOW,fetch=lambda ts,*a:{t:base if t=='^GSPC' else stock for t in ts},event_fetch=events)
            self.assertEqual(recovered['health']['status'],'OK')
            self.assertEqual({p['ticker'] for p in recovered['plans']},cohort)
            self.assertTrue(recovered['coverage']['retrying_failed_cohort'])
            self.assertEqual(recovered['coverage']['next_cursor'],bad['coverage']['retry_next_cursor'])
            self.assertTrue(all(t['observed_at']==NOW.isoformat() for t in recovered['trades']))

    def test_pending_latest_close_waits_for_later_wave_without_40_pointless_retries(self):
        stock,base=bars();bad=stock.copy()
        bad.loc[bad.index[-1],['Close','Adj Close']]=float('nan')
        self.assertTrue(pending_daily_bar(bad,'us',NOW))
        bad_history=bad.copy();bad_history.loc[bad.index[-2],'High']=float('nan')
        self.assertFalse(pending_daily_bar(bad_history,'us',NOW))
        calls=[]
        def download(ts,**kw):
            calls.append(ts)
            return base if ts==['^GSPC'] else pd.concat({t:bad for t in ts},axis=1).swaplevel(0,1,axis=1)
        with patch('price_feed.yf.download',side_effect=download):
            frames=fetch_bars(['^GSPC']+[str(i) for i in range(45)],'us',NOW)
        self.assertEqual(len(calls),4)
        self.assertEqual(frames.diagnostics['0']['readiness'],'DAILY_PENDING')
        self.assertTrue(pd.isna(frames['0'].Close.iloc[-1]))

    def test_pause_does_not_apply_next_stop_and_recovery_clears_old_reason(self):
        trade=dict(guarded(),state='OPEN',fill=100,stop=95,initial_stop=95,initial_risk=5,
                   target=120,held_sessions=1,last_processed='2026-09-14',next_stop=100.2)
        frame=next_bars(103,104,102,103);frame.index=pd.to_datetime(['2026-09-15']);frame['Dividends']=1.
        paused=advance_trade(trade,frame,'us','2026-09-15T22:00Z')
        self.assertEqual(paused['state'],'DATA_REVIEW');self.assertEqual(paused['stop'],95)
        self.assertEqual(paused['next_stop'],100.2)
        frame['Dividends']=0.;frame['Low']=94.
        closed=advance_trade(paused,frame,'us','2026-09-15T22:00Z')
        self.assertEqual(closed['state'],'CLOSED');self.assertNotIn('data_reason',closed)
        closed['data_reason']='obsolete legacy error'
        self.assertNotIn('data_reason',advance_trade(closed,None,'us','2026-09-15T22:00Z'))


class PipelineTests(unittest.TestCase):
    def test_current_snapshot_does_not_rotate_twice(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);snapshot(root)
            scanner=Mock()
            self.assertEqual(refresh('us',root,now=NOW,scanner=scanner)['status'],'CURRENT')
            scanner.assert_not_called()

    def test_legacy_bad_snapshot_gets_repaired_before_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);snapshot(root,failed=298)
            scanner=Mock(side_effect=lambda *a:snapshot(root))
            self.assertEqual(refresh('us',root,'premarket','2026-09-14T12:00Z',scanner=scanner)['status'],'REFRESHED')
            scanner.assert_called_once()
            self.assertEqual(deliver('us',root,'premarket','2026-09-14T12:00Z',sender=Mock())['status'],'SENT')

    def test_stale_or_forced_snapshot_refreshes_and_failure_visible(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);snapshot(root)
            scanner=Mock(side_effect=lambda *a:snapshot(root,failed=298))
            self.assertEqual(refresh('us',root,now=NOW,force=True,scanner=scanner)['status'],'FAILED')
            scanner.reset_mock()
            self.assertEqual(refresh('us',root,now='2026-09-14T22:00Z',scanner=scanner)['status'],'FAILED')
            scanner.assert_called_once()

    def test_holiday_skips_value_scan_and_after_close_skips_obsolete_brief(self):
        with tempfile.TemporaryDirectory() as d:
            scanner=Mock()
            self.assertEqual(refresh('us',d,'premarket','2026-09-07T12:00Z',scanner=scanner),dict(status='SKIPPED_HOLIDAY',run_value=False))
            self.assertEqual(refresh('us',d,'premarket','2026-09-14T21:00Z',scanner=scanner)['status'],'SKIPPED_ENDED')
            scanner.assert_not_called()

    def test_managed_full_scan_cannot_send_second_email(self):
        import main
        with patch.object(main,'detect_mode',return_value='premarket_scan'),patch.dict('os.environ',{'DAILY_BRIEF_MANAGED':'true','DRY_RUN':'false'}),patch.object(main,'run_full_scan',return_value=pd.DataFrame()),patch.object(main,'generate_report',return_value='<html>value</html>'),patch.object(main,'save_report_files'),patch('brief_delivery.deliver') as delivery,patch.object(main,'send_email') as smtp:
            main.main()
        delivery.assert_not_called();smtp.assert_not_called()


class OutboxTests(unittest.TestCase):
    def test_claim_is_persisted_before_smtp_and_success_receipt_after(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);snapshot(root);events=[]
            def persist_claim():
                marker=json.loads((root/'mos_brief_delivery.json').read_text())
                events.append('claim' if marker['pending'] else 'receipt')
            result=deliver('us',root,'after-close',NOW,sender=lambda *a:events.append('smtp'),persist_receipt=persist_claim)
            self.assertEqual(events,['claim','smtp','receipt']);self.assertEqual(result['status'],'SENT')

    def test_failed_claim_push_does_not_send(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);snapshot(root);sender=Mock()
            with self.assertRaises(RuntimeError):
                deliver('us',root,'after-close',NOW,sender=sender,persist_receipt=Mock(side_effect=RuntimeError('Git test')))
            sender.assert_not_called()

    def test_post_smtp_push_failure_leaves_remote_claim_blocking_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);snapshot(root);remote={};sender=Mock()
            def save():
                marker=json.loads((root/'mos_brief_delivery.json').read_text())
                if not marker['pending']:
                    raise RuntimeError('Git test')
                remote.update(marker)
            with self.assertRaises(RuntimeError):
                deliver('us',root,'after-close',NOW,sender=sender,persist_receipt=save)
            (root/'mos_brief_delivery.json').write_text(json.dumps(remote))
            self.assertEqual(deliver('us',root,'after-close',NOW,sender=sender)['status'],'DELIVERY_REVIEW')
            sender.assert_called_once()

    def test_private_files_never_staged_and_push_race_retried(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'data').mkdir()
            (root/'state/mos_brief_delivery.json').write_text('{}')
            (root/'data/account_risk.private.json').write_text('{"net_worth":123}')
            calls=[];pushes=[]
            def command(args,check):
                calls.append(args)
                code=1 if args[:2]==['diff','--cached'] else 0
                if args[0]=='push':
                    pushes.append(args);code=int(len(pushes)==1)
                return Mock(returncode=code)
            persist('us','receipt',root,command=command,pause=lambda *a:None)
            self.assertIn(['add','--','state/mos_brief_delivery.json'],calls)
            self.assertEqual(len(pushes),2)
            self.assertNotIn('private',str(calls))
            self.assertIn(['commit','-m','update us public receipt state','--only','--','state/mos_brief_delivery.json'],calls)
