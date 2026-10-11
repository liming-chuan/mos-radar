import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from test_short_term import bars, NOW, next_bars, plan
from test_safety import good_row
import main
from price_feed import fetch_bars
from scan_checkpoint import load_checkpoint, save_checkpoint
from short_term import advance_trade, ledger_html
from test_entry_guards import guarded


class ValueRuntimeTests(unittest.TestCase):
    def test_empty_config_uses_default_but_explicit_false_remains_false(self):
        with patch.dict(os.environ, {'USE_FUNDAMENTALS_CACHE':' ', 'DRY_RUN':''}):
            self.assertTrue(main.cache_enabled())
            self.assertFalse(main.env_bool('DRY_RUN', False))
        with patch.dict(os.environ, {'USE_FUNDAMENTALS_CACHE':'false'}):
            self.assertFalse(main.cache_enabled())

    def test_checkpoint_rejects_stale_scope_and_wrong_order(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'progress.json'
            save_checkpoint(path,'scope',[dict(ticker='A')])
            self.assertEqual(len(load_checkpoint(path,'scope',['A','B'],7)),1)
            self.assertEqual(load_checkpoint(path,'new',['A','B'],7),[])
            self.assertEqual(load_checkpoint(path,'scope',['B','A'],7),[])
            value=json.loads(path.read_text());value['started_at']='2020-01-01T00:00:00+00:00'
            path.write_text(json.dumps(value))
            self.assertEqual(load_checkpoint(path,'scope',['A','B'],7),[])

    def test_budget_pause_resumes_and_only_complete_scan_publishes(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'VALUE_SCAN_BATCH_SIZE':'1', 'VALUE_SCAN_BUDGET_SECONDS':'60'}), \
             patch.object(main,'CACHE_DIR',Path(d)/'cache'), patch.object(main,'STATE_MARKET_PATH',Path(d)/'state.csv'), \
             patch.object(main,'load_scan_tickers',return_value=['A','B']), \
             patch.object(main,'analyze_one',side_effect=lambda t,**kw:dict(good_row(ticker=t),cache_status='MISS')) as analyze, \
             patch.object(main,'annotate_pools',side_effect=lambda df:df), \
             patch.object(main,'annotate_opportunities',side_effect=lambda df,**kw:df), \
             patch.object(main,'enrich_watchlists',side_effect=lambda df,**kw:df), \
             patch.object(main,'update_prices_only',side_effect=lambda df,**kw:df) as prices, \
             patch.object(main,'save_outputs') as save:
            first=main.run_full_scan()
            self.assertEqual(first.scan_status.iloc[0],'PARTIAL_BUDGET')
            self.assertFalse(save.call_args.kwargs['write_state'])
            prices.assert_not_called()
            second=main.run_full_scan()
            self.assertEqual(second.ticker.tolist(),['A','B'])
            self.assertTrue(second.scan_status.eq('COMPLETE').all())
            self.assertTrue(save.call_args.kwargs['write_state'])
            self.assertEqual([c.args[0] for c in analyze.call_args_list],['A','B'])
            prices.assert_called_once()
            self.assertFalse((Path(d)/'cache/_scan_progress.json').exists())

    def test_cache_hits_do_not_exhaust_fresh_request_budget(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'VALUE_SCAN_BATCH_SIZE':'1'}), \
             patch.object(main,'CACHE_DIR',Path(d)/'cache'), patch.object(main,'STATE_MARKET_PATH',Path(d)/'state.csv'), \
             patch.object(main,'load_scan_tickers',return_value=['A','B','C']), \
             patch.object(main,'analyze_one',side_effect=lambda t,**kw:dict(good_row(ticker=t),cache_status='HIT')), \
             patch.object(main,'annotate_pools',side_effect=lambda df:df), \
             patch.object(main,'annotate_opportunities',side_effect=lambda df,**kw:df), \
             patch.object(main,'enrich_watchlists',side_effect=lambda df,**kw:df), \
             patch.object(main,'update_prices_only',side_effect=lambda df,**kw:df), patch.object(main,'save_outputs'):
            self.assertTrue(main.run_full_scan().scan_status.eq('COMPLETE').all())

    def test_checkpoint_rejects_rows_past_cache_ttl(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'progress.json'
            save_checkpoint(path,'scope',[dict(ticker='A',cache_age_days=8)])
            self.assertEqual(load_checkpoint(path,'scope',['A','B'],7),[])

    def test_time_budget_stops_between_symbols_and_retains_public_state(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'VALUE_SCAN_BUDGET_SECONDS':'60'}), \
             patch.object(main,'CACHE_DIR',Path(d)/'cache'),patch.object(main,'STATE_MARKET_PATH',Path(d)/'state.csv'), \
             patch.object(main,'load_scan_tickers',return_value=['A','B']), \
             patch.object(main,'analyze_one',side_effect=lambda t,**kw:dict(good_row(ticker=t),cache_status='MISS')) as analyze, \
             patch.object(main.time,'monotonic',side_effect=[0,0,46]), \
             patch.object(main,'annotate_pools',side_effect=lambda df:df), \
             patch.object(main,'annotate_opportunities',side_effect=lambda df,**kw:df), \
             patch.object(main,'enrich_watchlists',side_effect=lambda df,**kw:df),patch.object(main,'save_outputs') as save:
            result=main.run_full_scan()
            self.assertEqual(result.scan_status.iloc[0],'PARTIAL_BUDGET')
            analyze.assert_called_once()
            self.assertFalse(save.call_args.kwargs['write_state'])


class PriceRecoveryTests(unittest.TestCase):
    def test_price_refresh_stops_between_batches_without_fabricating_quotes(self):
        import price_update
        with patch('price_update.time.monotonic',side_effect=[0,0,301]), \
             patch('price_update._batch_quotes',return_value={'0':(10.,'2026-09-11')}) as fetch:
            result=price_update.batch_current_quotes([str(i) for i in range(120)])
            self.assertEqual(len(fetch.call_args.args[0]),50)
            fetch.assert_called_once()
            self.assertEqual(result,{'0':(10.,'2026-09-11')})

    def test_history_fallback_is_validated_and_same_session_cache_avoids_download(self):
        stock,base=bars();bad=base.copy();bad.loc[bad.index[-1],['Close','Adj Close']]=float('nan')
        history=Mock();history.history.return_value=base
        with tempfile.TemporaryDirectory() as d:
            with patch('price_feed.yf.download',return_value=bad),patch('price_feed.yf.Ticker',return_value=history):
                frames=fetch_bars(['^GSPC'],'us',NOW,cache_root=d,fallback=True)
            self.assertEqual(frames.diagnostics['^GSPC']['source'],'YAHOO_HISTORY')
            self.assertFalse(history.history.call_args.kwargs['repair'])
            with patch('price_feed.yf.download') as download:
                same=fetch_bars(['^GSPC'],'us',NOW,cache_root=d)
                download.assert_not_called()
                self.assertEqual(same.diagnostics['^GSPC']['source'],'VALIDATED_CACHE')
            # Yesterday's complete data can never authorize today's plans.
            with patch('price_feed.yf.download',return_value=pd.DataFrame()) as download:
                stale=fetch_bars(['^GSPC'],'us','2026-09-14T22:00Z',cache_root=d)
                self.assertTrue(stale.diagnostics['^GSPC']['reason'])
                self.assertTrue(download.called)

    def test_invalid_fallback_does_not_fill_missing_close_or_save_cache(self):
        _,base=bars();bad=base.copy();bad.loc[bad.index[-1],['Close','Adj Close']]=float('nan')
        history=Mock();history.history.return_value=bad
        with tempfile.TemporaryDirectory() as d,patch('price_feed.yf.download',return_value=bad),patch('price_feed.yf.Ticker',return_value=history):
            frames=fetch_bars(['^GSPC'],'us',NOW,cache_root=d,fallback=True)
            self.assertEqual(frames.diagnostics['^GSPC']['readiness'],'DAILY_PENDING')
            self.assertTrue(pd.isna(frames['^GSPC'].iloc[-1].Close))
            self.assertEqual(list(Path(d).rglob('*.json')),[])


class EntryDiagnosticsTests(unittest.TestCase):
    def test_expired_reasons_and_execution_evidence_are_distinct(self):
        p=dict(guarded(),state='PENDING')
        cases=[(next_bars(p['trigger']-.5,p['trigger']-.1,p['stop']+.1,p['trigger']-.3),'NOT_TRIGGERED'),
               (next_bars(p['max_entry']+1,p['max_entry']+2,p['trigger'],p['max_entry']+1),'ABOVE_ENTRY_LIMIT'),
               (next_bars(p['stop']-1,p['trigger']+1,p['stop']-2,p['trigger']),'OPEN_BELOW_STOP')]
        for frame,code in cases:
            result=advance_trade(p,frame,'us','2026-09-14T22:00Z')
            self.assertEqual(result['state'],'EXPIRED')
            self.assertEqual(result['expiry_code'],code)
            self.assertIn('estimated_fill',result['entry_evidence'])
            self.assertEqual(advance_trade(result,frame,'us','2026-09-14T22:00Z'),result)
        fixed=result;be=dict(result,exit_policy='BE_1R')
        self.assertIn('累计未成交计划 1组',ledger_html([fixed,be]))


if __name__=='__main__':
    unittest.main()
