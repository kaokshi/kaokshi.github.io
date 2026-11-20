"""LanguageTool compatibility and fail-closed diagnostics."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_language_tool import serialize_match

class LanguageToolSerializationTests(unittest.TestCase):
 def test_current_length(self):
  m=SimpleNamespace(rule_id="X",message="Fix",offset=4,length=2,replacements=["new"])
  item=serialize_match(m)
  self.assertEqual(item["length"],2)
  self.assertEqual(item["offset"],4)
 def test_legacy_error_length(self):
  m=SimpleNamespace(rule_id="X",message="Fix",offset=7,error_length=3,replacements=[])
  self.assertEqual(serialize_match(m)["length"],3)
 def test_no_silent_zero_when_length_missing(self):
  m=SimpleNamespace(rule_id="X",message="Fix",offset=7,replacements=[])
  with self.assertRaisesRegex(ValueError,"unavailable"):
   serialize_match(m)
 def test_no_silent_invalid_offset(self):
  m=SimpleNamespace(length=2,offset=-1)
  with self.assertRaises(ValueError):
   serialize_match(m)

if __name__=="__main__":
 unittest.main()
