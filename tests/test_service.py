import asyncio, json, os, tempfile, unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi.testclient import TestClient
from backend import service as s

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();s.DB_PATH=self.temp.name+'/state.sqlite3';s.TOKEN='test-only-token-not-a-real-secret';s.snapshots.clear();s.cache.clear();s.initialize()
    def tearDown(self):self.temp.cleanup()
    def test_four_profiles_and_cspx_identity(self):
        self.assertEqual(s.config(1)['conid'],76023663);self.assertEqual(s.config(1)['exchange'],'LSEETF')
        for i in (2,3,4):self.assertIsNone(s.config(i)['conid'])
    def test_history_persistence_and_profile_isolation(self):
        with s.db() as c:
            for _ in range(2):c.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',('unique',1,'2026-10-07',json.dumps({'ticker':'CSPX'})))
        self.assertEqual(asyncio.run(s.get_history(1))['total'],1)
        self.assertEqual(asyncio.run(s.get_history(2))['total'],0)
        s.initialize();self.assertEqual(asyncio.run(s.get_history(1))['total'],1)
    def test_auth_and_private_file_protection(self):
        client=TestClient(s.app)
        self.assertEqual(client.get('/api/profiles').status_code,401)
        self.assertEqual(client.get('/api/profiles',headers={'Authorization':'Bearer '+s.TOKEN}).status_code,200)
        self.assertEqual(client.get('/break-retest.sqlite3').status_code,404)
        self.assertEqual(client.get('/backend/service.py').status_code,404)
        self.assertEqual(client.post('/api/orders',headers={'Authorization':'Bearer '+s.TOKEN}).status_code,404)
    def test_offline_service_returns_no_entry(self):
        async def unavailable():raise ConnectionError('TWS no conectado')
        with patch.object(s,'connect',unavailable):asyncio.run(s.build(1))
        self.assertFalse(s.snapshots[1]['entry_available']);self.assertIsNone(s.snapshots[1]['price'])
    def test_startup_cannot_reactivate_saved_signal(self):
        with s.db() as c:c.execute('INSERT INTO snapshots VALUES (?,?)',(1,json.dumps({'entry_available':True,'price':100})))
        s.initialize();self.assertFalse(s.snapshots[1]['entry_available']);self.assertIsNone(s.snapshots[1]['price'])
    def test_invalid_config(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):s.Settings(conid=1,exchange='NYSE',tolerance=float('nan'))
    def test_schedule_dst(self):
        self.assertEqual(s.parse_schedule_date('20261007-08:00:00','Europe/London').hour,7)
        self.assertEqual(s.parse_schedule_date('20261207-08:00:00','Europe/London').hour,8)

if __name__=='__main__':unittest.main()
