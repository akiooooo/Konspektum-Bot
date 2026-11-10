import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from konspektum.maintenance import backup


class MaintenanceTests(unittest.TestCase):
    def test_backup_is_consistent_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.db'
            target=Path(folder)/'backups'/'copy.db'
            with closing(sqlite3.connect(source)) as db,db:
                db.execute('CREATE TABLE test(value TEXT)')
                db.execute('INSERT INTO test VALUES(?)',('Конспектум',))
            backup(source,target)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute('SELECT value FROM test').fetchone()[0],'Конспектум')
            with self.assertRaises(ValueError):
                backup(source,target)
            with self.assertRaises(ValueError):
                backup(Path(folder)/'missing.db',Path(folder)/'other.db')
