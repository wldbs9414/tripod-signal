import copy
import json
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import auxiliary as aux
import telegram_daily as app

class AuxiliaryTests(unittest.TestCase):
    def setUp(self):
        self.strategy=json.loads(app.SIGNAL.read_text())['strategies']['tripod']
        self.dates=[str(i) for i in range(400)]
        self.closes=[float(i+1) for i in range(400)]
        self.vix=[10+i*.01 for i in range(400)]
        self.strategy['latest']['date']=self.dates[-1]
    def calc(self):return aux.calculate(self.strategy,(self.dates,self.closes,self.vix,0))
    def test_all_means_and_changes_against_independent_math(self):
        result=self.calc()
        for n in aux.MA_WINDOWS:
            self.assertAlmostEqual(result['moving_averages'][str(n)],sum(self.closes[-n:])/n,places=5)
        self.assertEqual(result['long_position'],'상단');self.assertEqual(result['ma50_vs_ma200'],'상회')
        self.assertEqual(result['drawdown_pct'],0);self.assertAlmostEqual(result['vix_change'],.01)
    def test_falling_prices_and_falling_vix(self):
        self.closes=list(reversed(self.closes));self.vix=list(reversed(self.vix));a=self.calc()
        self.assertEqual(a['long_position'],'하단');self.assertEqual(a['ma50_vs_ma200'],'하회');self.assertLess(a['drawdown_pct'],0);self.assertLess(a['vix_change'],0)
    def test_does_not_use_future_source_rows(self):
        before=self.calc();self.dates.append('future');self.closes.append(999999.0);self.vix.append(999999.0)
        self.assertEqual(before,self.calc())
    def test_missing_date_and_short_history_do_not_invent_values(self):
        self.strategy['latest']['date']='missing';self.assertFalse(self.calc()['available'])
        self.strategy['latest']['date']='10';self.assertFalse(self.calc()['available'])
    def test_nan_and_misaligned_data_are_unavailable(self):
        self.closes[-1]=float('nan');self.assertFalse(self.calc()['available'])
        self.closes[-1]=400;self.vix.pop();self.assertFalse(self.calc()['available'])
    def test_equal_lines_are_contact_not_uptrend(self):
        self.closes=[100.0]*400;a=self.calc()
        self.assertEqual(a['long_position'],'구간 내·선 접촉');self.assertEqual(a['ma50_vs_ma200'],'동일')
    def test_rolling_peak_expiry_is_disclosed(self):
        self.closes=[100.0]*400;self.closes[-253]=200.0;a=self.calc()
        self.assertTrue(a['peak_changed']);self.assertGreater(a['drawdown_change_pp'],0)
        self.assertIn('고점 기준이 전일과 달라져',aux.describe(a,self.strategy))
    def test_supplement_does_not_mutate_signal(self):
        before=copy.deepcopy(self.strategy);a=self.calc();app.build_message(self.strategy,a)
        self.assertEqual(before,self.strategy)
    def test_supplement_is_saved_in_fingerprint(self):
        a=self.calc();b=copy.deepcopy(a);b['moving_averages']['225']+=1
        self.assertNotEqual(app.digest(self.strategy,a),app.digest(self.strategy,b))
    def test_real_source_agrees_with_existing_indicators(self):
        strategy=json.loads(app.SIGNAL.read_text())['strategies']['tripod'];a=aux.calculate(strategy)
        self.assertTrue(a['available']);l=strategy['latest']
        self.assertAlmostEqual(a['moving_averages']['250'],l['legs']['ma']['value'],places=2)
        self.assertAlmostEqual(a['vix10'],l['legs']['vix']['value'],places=2)
        self.assertAlmostEqual(a['drawdown_pct'],l['legs']['dd']['value'],places=2)
        message=app.build_message(strategy,a)
        self.assertLess(len(message.encode('utf-16-le'))//2,4096)
        for heading in ['①','②','③','해석:']:self.assertIn(heading,message)
    def test_real_source_series_matches_original_replay(self):
        strategy=json.loads(app.SIGNAL.read_text())['strategies']['tripod'];built=aux.original_signal().build()
        lookup={r['d']:r for r in built['rows']}
        for r in strategy['series'][-30:]:
            probe=copy.deepcopy(strategy);probe['latest']['date']=r['d'];a=aux.calculate(probe);row=lookup[r['d']]
            self.assertAlmostEqual(a['moving_averages']['250'],row['ma'],places=2)
            self.assertAlmostEqual(a['vix10'],row['v'],places=2)
            self.assertAlmostEqual(a['drawdown_pct'],100*row['dd'],places=2)

if __name__=='__main__':unittest.main()
