import sqlite3
import unittest
from src.zen_dzen_storage import import_to_db, history
from test_zen_dzen_xlsx import fixture

class StorageTest(unittest.TestCase):
    def test_replay_and_history(self):
        db=sqlite3.connect(":memory:")
        raw=fixture()
        self.assertTrue(import_to_db(db,raw,"science","a"*24)["created"])
        self.assertFalse(import_to_db(db,raw,"science","a"*24)["created"])
        self.assertEqual(len(history(db,"science")),1)
        self.assertEqual(history(db,"cinema"),[])
    def test_binding_required(self):
        db=sqlite3.connect(":memory:")
        with self.assertRaises(ValueError): import_to_db(db,fixture(),"science",None)
    def test_cross_channel_reuse(self):
        db=sqlite3.connect(":memory:")
        import_to_db(db,fixture(),"science","a"*24)
        with self.assertRaises(ValueError): import_to_db(db,fixture("cinema"),"cinema","a"*24)
        self.assertEqual(history(db,"cinema"),[])
