import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from impact import storage
from tools.migrate_to_supabase import TABLES,fingerprint,migrate


class StorageTests(unittest.TestCase):
    def test_remote_failure_never_creates_local_fallback_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'should_not_exist.sqlite3'
            with patch('impact.storage._pool',side_effect=storage.DatabaseUnavailable('Unavailable')):
                with self.assertRaises(storage.DatabaseUnavailable):
                    with storage.connection(path,'business',url='postgresql://remote.example/postgres'): pass
            self.assertFalse(path.exists())

    def test_remote_plaintext_and_invalid_urls_are_rejected_without_secret_in_error(self):
        for url in ('postgresql://user:PRIVATE_VALUE@remote.example/postgres?sslmode=disable',
                    'https://user:PRIVATE_VALUE@remote.example'):
            with self.subTest(url_type=url.split(':')[0]):
                with self.assertRaises(storage.DatabaseUnavailable) as caught: storage._pool(url)
                self.assertNotIn('PRIVATE_VALUE',str(caught.exception))

    def test_sqlite_rolls_back_transaction_and_enforces_foreign_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'local.sqlite3'
            with storage.connection(path,'business') as conn:
                conn.executescript('CREATE TABLE sample(id TEXT PRIMARY KEY);')
            with self.assertRaises(ValueError):
                with storage.connection(path,'business') as conn:
                    conn.execute('INSERT INTO sample VALUES (?)',('data',))
                    raise ValueError('rollback')
            with storage.connection(path,'business') as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sample').fetchone()[0],0)
                self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0],1)

    def test_record_supports_mapping_and_numeric_access(self):
        row=storage.Record({'id':7,'payload':'data'})
        self.assertEqual(row[0],7); self.assertEqual(row['payload'],'data')
        self.assertEqual(dict(row),{'id':7,'payload':'data'})

    def test_migration_fingerprint_ignores_database_collation(self):
        first={'a':[{'id':'b','payload':'?%'},{'id':'a','payload':'é'}]}
        self.assertEqual(fingerprint(first),fingerprint({'a':list(reversed(first['a']))}))

    def test_migration_simulation_and_missing_target_never_connect(self):
        snapshot={table:[] for table in TABLES}
        with patch('tools.migrate_to_supabase.read_snapshot',return_value=snapshot),patch('impact.db.init') as initialize:
            report=migrate('source.sqlite3')
            self.assertEqual(report['ai_calls'],0); self.assertEqual(report['mode'],'simulation')
            with self.assertRaises(ValueError): migrate('source.sqlite3',apply=True)
            initialize.assert_not_called()


if __name__=='__main__': unittest.main(verbosity=2)
