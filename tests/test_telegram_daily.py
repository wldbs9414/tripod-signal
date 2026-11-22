import copy
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import telegram_daily as app
from params import orders_for

class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original=json.loads(app.SIGNAL.read_text())
        self.payload=copy.deepcopy(self.original)
        self.day=self.payload['strategies']['tripod']['latest']['date']
        self.now=dt.datetime.fromisoformat(self.day+'T23:30:00+00:00')
        self.signal=Path(self.temp.name)/'signal.json'
        self.state=Path(self.temp.name)/'state.json'
        for key,value in [('SIGNAL',self.signal),('STATE',self.state),('HISTORY',Path(self.temp.name)/'history.jsonl')]:
            p=patch.object(app,key,value);p.start();self.addCleanup(p.stop)
        self.write()
    def write(self):self.signal.write_text(json.dumps(self.payload))
    def latest(self):return self.payload['strategies']['tripod']['latest']
    def test_dry_run_never_sends_or_saves(self):
        with patch.object(app,'safe_send') as send:
            message=app.run(dry_run=True,now=self.now)
        send.assert_not_called();self.assertFalse(self.state.exists());self.assertIn(self.day,message);self.assertNotIn('변동성 타겟',message)
    def test_success_once_per_session(self):
        with patch.object(app,'safe_send') as send:
            app.run(now=self.now);result=app.run(now=self.now)
        self.assertEqual(send.call_count,1);self.assertIn('already sent',result)
    def test_failed_delivery_does_not_mark_sent(self):
        with patch.object(app,'safe_send',side_effect=RuntimeError('not confirmed')):
            with self.assertRaises(RuntimeError):app.run(now=self.now)
        self.assertFalse(self.state.exists())
    def test_revisions_and_reversions_are_reported(self):
        with patch.object(app,'safe_send') as send:
            app.run(now=self.now)
            self.latest()['legs']['vix']['value']+=.01;self.write();app.run(now=self.now)
            app.run(now=self.now)
            self.latest()['legs']['vix']['value']-=.01;self.write();app.run(now=self.now)
        self.assertEqual(send.call_count,3)
    def test_old_data_is_status_not_a_trade(self):
        now=self.now+dt.timedelta(days=7)
        with patch.object(app,'safe_send') as send:
            app.run(now=now);app.run(now=now)
        self.assertEqual(send.call_count,1);message=send.call_args.args[0]
        self.assertIn('미확보',message)
        self.assertNotIn('전량 매도',message)
    def test_provisional_rejected(self):
        self.payload['provisional']={'usable':True};self.write()
        with self.assertRaises(ValueError):app.run(dry_run=True,now=self.now)
    def test_invalid_indicator_rejected(self):
        self.latest()['legs']['vix']['value']=float('nan');self.write()
        with self.assertRaises(ValueError):app.run(dry_run=True,now=self.now)
    def test_state_only_is_not_an_order(self):
        l=self.latest();l.update(gear='G15_DOWN',prev_gear='G15_UP',action_required=False,regime_only_change=True,orders=orders_for('G15_UP','G15_DOWN'));self.write()
        message=app.run(dry_run=True,now=self.now)
        self.assertIn('상태만 변경',message);self.assertNotIn('<b>주문</b>',message)
    def test_failure_notice_distinct_and_deduplicated(self):
        with patch.object(app,'safe_send') as send:
            app.run(fail=True,now=self.now);app.run(fail=True,now=self.now)
        self.assertEqual(send.call_count,1);self.assertIn('실패',send.call_args.args[0])
    def test_us_weekend_and_before_close(self):
        self.assertEqual(app.expected_weekday(dt.datetime(2026,11,23,8,tzinfo=dt.timezone.utc)),'2026-11-20')
        self.assertEqual(app.expected_weekday(dt.datetime(2026,11,23,23,tzinfo=dt.timezone.utc)),'2026-11-23')
    def test_missing_bot_settings(self):
        with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':''}):
            with self.assertRaisesRegex(RuntimeError,'Secrets'):app.safe_send('test')
    def test_token_not_in_http_error(self):
        secret='TEST-DO-NOT-LOG'
        error=urllib.error.HTTPError('https://api.telegram.org/bot'+secret+'/sendMessage',401,'Unauthorized',{},None)
        with patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':secret,'TELEGRAM_CHAT_ID':'123'}),patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(RuntimeError) as e:app.safe_send('test')
        self.assertNotIn(secret,str(e.exception));self.assertIn('401',str(e.exception))
    def test_test_message_not_a_signal(self):
        with patch.object(app,'safe_send') as send:app.run(test=True,now=self.now)
        self.assertIn('매매 신호가 아니에요',send.call_args.args[0])

if __name__=='__main__':unittest.main()
