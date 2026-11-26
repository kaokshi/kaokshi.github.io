import io
import unittest
import zipfile
from xml.sax.saxutils import escape
from src.zen_dzen_xlsx import HEADERS, CHANNELS, import_report

def fixture(channel="science", duplicate=False, wrong_total=False):
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Статистика" r:id="r1"/><sheet name="Дзен" r:id="r2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Target="worksheets/sheet2.xml"/></Relationships>')
        for i, n in ((1,12),(2,9)):
            plat="Все платформы" if i==1 else "Дзен"
            total=["Всего",plat,None,None,None,10,2,1,3,0,4,1][:n]
            row=[46349.2,plat,"Статья","Synthetic fixture","https://dzen.ru/media/id/"+"a"*24+"/"+"b"*24,10,2,1,3,0,0,1][:n]
            table={1:['Публикации канала "'+CHANNELS[channel]+'", по времени события, период 2026-08-29 - 2026-11-26'],3:HEADERS[:n],4:total,5:row}
            if duplicate: table[6]=row
            xml='<worksheet><sheetData>'
            for r, vals in table.items():
                xml+='<row>'
                for c,v in enumerate(vals):
                    if v is None: continue
                    ref=chr(65+c)+str(r)
                    xml+=f'<c r="{ref}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' if isinstance(v,str) else f'<c r="{ref}"><v>{v}</v></c>'
                xml+='</row>'
            z.writestr(f"xl/worksheets/sheet{i}.xml",xml+'</sheetData></worksheet>')
    return out.getvalue()

class ImportTest(unittest.TestCase):
    def test_all_channels_and_separate_totals(self):
        for c in CHANNELS:
            r=import_report(fixture(c),c)
            self.assertEqual(r["all_platforms"]["totals"]["impressions"],10)
            self.assertEqual(r["dzen_platform"]["totals"]["impressions"],10)
            self.assertEqual(r["all_platforms"]["total_minus_publication_sum"]["subscriptions"],4)
            self.assertEqual(r["all_platforms"]["publication_count"],1)
            self.assertIsNone(r["ctr"])
            self.assertIsNone(r["revenue"])
    def test_replay(self):
        self.assertEqual(import_report(fixture(),"science")["snapshot_id"],import_report(fixture(),"science")["snapshot_id"])
    def test_binding(self):
        self.assertEqual(import_report(fixture(),"science","a"*24)["channel_binding"],"expected_id_matched")
        with self.assertRaises(ValueError): import_report(fixture(),"science","c"*24)
        with self.assertRaises(ValueError): import_report(fixture(),"cinema")
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError): import_report(fixture(duplicate=True),"science")
