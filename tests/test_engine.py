import unittest
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from backend.engine import Bar,analyze,levels_for,closed,ema,dt

OPEN='2026-10-07T13:30:00+00:00';CLOSE='2026-10-07T20:00:00+00:00'
def bar(i,o,h,l,c,v=None):return Bar((dt(OPEN)+timedelta(minutes=2*i)).isoformat(),o,h,l,c,v)
def run(bars,**kwargs):
    return analyze(bars,kwargs.pop('levels',[{'code':'PDH','label':'PDH · Previo Día Alto','price':100,'available_at':OPEN}]),kwargs.pop('now',CLOSE),OPEN,CLOSE,**kwargs)

class EngineTests(unittest.TestCase):
    def test_wick_is_not_break(self):
        r=run([bar(0,99,100.5,98.9,99.9)]);self.assertEqual(r['events'],[]);self.assertIsNone(r['entry'])
    def test_unclosed_bar_excluded(self):
        r=run([bar(0,99,101,98,100.5)],now=dt(OPEN)+timedelta(seconds=119));self.assertEqual(r['bars'],[])
    def test_exact_close_not_break(self):self.assertFalse(run([bar(0,99,101,98,100)])['events'])
    def test_break_no_entry(self):
        r=run([bar(0,99,101,98,100.5)]);self.assertEqual(r['state'],'break');self.assertIsNone(r['entry'])
    def test_no_chasing(self):
        r=run([bar(0,99,101,98,100.5),bar(1,100.5,103,100.4,102),bar(2,102,104,101.5,103)])
        self.assertEqual(r['state'],'await');self.assertIsNone(r['entry'])
    def test_long_stages_and_entry_only_later(self):
        b=[bar(0,99,101,98,100.5),bar(1,100.5,100.8,100,100.4)]
        r=run(b);self.assertEqual([e['state'] for e in r['events']],['break','await','retest']);self.assertEqual(r['entry'],100.4)
        r=run(b+[bar(2,100.4,102,100.3,101.8)]);self.assertEqual(r['state'],'continue');self.assertAlmostEqual(r['movement'],1.6)
    def test_short_symmetric(self):
        r=run([bar(0,101,102,99,99.5),bar(1,99.5,100,99.1,99.4),bar(2,99.4,99.5,98,98.1)])
        self.assertEqual(r['state'],'continue');self.assertAlmostEqual(r['movement'],1.4)
    def test_touch_wrong_side_invalid(self):
        r=run([bar(0,99,101,98,100.5),bar(1,100.5,100.8,99.5,99.9)])
        self.assertEqual(r['state'],'invalid');self.assertIsNone(r['entry'])
    def test_close_at_level_not_defended(self):
        r=run([bar(0,99,101,98,100.5),bar(1,100.5,100.8,100,100)])
        self.assertEqual(r['state'],'await');self.assertIsNone(r['entry'])
    def test_missing_optional_indicators_does_not_block(self):
        r=run([bar(0,99,101,98,100.5),bar(1,100.5,100.8,100,100.4)])
        self.assertEqual(r['state'],'retest');self.assertIsNone(r['confirmations']['vwap']);self.assertIsNone(r['confirmations']['ema21'])
    def test_gap_is_not_retest(self):
        r=run([bar(0,99,101,98,100.5),bar(3,100.5,100.8,100,100.4)])
        self.assertEqual(r['state'],'invalid');self.assertIsNone(r['entry'])
    def test_exact_tolerance_default(self):
        bs=[bar(0,99,101,98,100.5),bar(1,100.5,100.8,100.04,100.4)]
        self.assertIsNone(run(bs)['entry']);self.assertEqual(run(bs,tolerance=.05)['state'],'retest')
    def test_tolerance_does_not_change_break_or_invalidation(self):
        self.assertEqual(run([bar(0,99,101,98,100.01)],tolerance=1)['state'],'break')
        self.assertEqual(run([bar(0,99,101,98,100.5),bar(1,100.5,101,99.98,99.99)],tolerance=1)['state'],'invalid')
    def test_london_before_opening_range(self):
        ls=levels_for('LONDON',[bar(0,99,100,95,98)],[],[],OPEN)
        r=run([bar(0,99,101,98,100.5),bar(1,100.5,100.8,100,100.4)],levels=ls)
        self.assertEqual(r['state'],'retest');self.assertEqual([l['code'] for l in ls],['PDH','PDL','ORH','ORL'])
    def test_no_or_lookahead(self):
        ls=levels_for('LONDON',[],[],[bar(0,99,100,95,98)],OPEN)
        self.assertFalse(run([bar(0,99,101,98,100.5)],levels=ls)['events'])
        self.assertFalse(run([bar(7,99,101,98,100.5)],levels=ls)['events']) # 14-16 straddles 15
        self.assertEqual(run([bar(8,99,101,98,100.5)],levels=ls)['state'],'break')
    def test_us_no_or(self):
        ls=levels_for('US',[bar(0,99,100,95,98)],[bar(0,100,102,96,99)],[],OPEN)
        self.assertEqual([l['code'] for l in ls],['PDH','PDL','PMH','PML']);self.assertEqual(ls[2]['price'],102)
    def test_duplicates_idempotent(self):
        b=[bar(0,99,101,98,100.5),bar(1,100.5,100.8,100,100.4)]
        self.assertEqual(run(b),run(b+b));self.assertEqual(run(b),run(b))
    def test_bad_data_rejected(self):
        with self.assertRaises(ValueError):run([bar(0,99,98,100,101)])
    def test_timezone_dst(self):
        summer=datetime(2026,7,1,8,tzinfo=ZoneInfo('Europe/London')).astimezone(ZoneInfo('America/Lima'))
        winter=datetime(2026,12,1,8,tzinfo=ZoneInfo('Europe/London')).astimezone(ZoneInfo('America/Lima'))
        self.assertEqual((summer.hour,winter.hour),(2,3))
    def test_reference_must_be_synchronous(self):
        r=run([bar(0,99,101,98,100.5)],reference=[bar(10,99,101,98,100.5)])
        self.assertEqual(r['confirmations']['cross'],'DATO NO DISPONIBLE')
    def test_15m_confirmation_optional_and_closed(self):
        b=[bar(0,99,101,98,100.5)]
        self.assertIsNone(run(b,context=b,now=dt(OPEN)+timedelta(minutes=2))['confirmations']['fifteen'])
    def test_vwap_uses_trade_weighted_averages(self):
        b=[Bar(OPEN,99,101,98,100.5,10,100)]
        self.assertEqual(run(b)['confirmations']['vwap'],100)
    def test_no_afterhours_setups(self):
        self.assertEqual(run([bar(200,99,101,98,100.5)])['events'],[])
    def test_each_level_independent(self):
        ls=levels_for('US',[bar(0,99,100,95,98)],[bar(0,98,99,96,99)],[],OPEN)
        r=run([bar(0,98,101,97,100.5)],levels=ls)
        self.assertEqual(len([e for e in r['events'] if e['state']=='break']),2)

if __name__=='__main__':unittest.main()
