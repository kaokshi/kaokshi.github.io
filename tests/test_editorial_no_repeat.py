"""No-repeat policy across published, draft and paraphrased editorial topics."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_no_repeat import topic_duplicate_reason
from editorial_nine_draft_queue import validate_plan
from editorial_writer import write_article

class NoRepeatPolicy(unittest.TestCase):
    def test_ten_existing_topics_are_rejected(self):
        old={
            "legends":["Проходная дома №17: кто расписывался в журнале",
                       "Последняя электричка, на которой никто не выходит",
                       "Лифт с двойным звонком: этаж которого нет",
                       "Загадочное окно на чердаке",
                       "Двор на старой фотографии"],
            "science":["Красные спрайты над грозами",
                       "Почему ночью светится море",
                       "Как тихоходки переживают полное высыхание",
                       "Невидимая темная материя в космосе",
                       "Откуда берётся полярное сияние"],
            "cinema":["Пересказ фильма Интерстеллар 2014",
                      "Разбор фильма Начало (2010)",
                      "Рецензия фильма Престиж 2006"],
        }
        for channel,titles in old.items():
            for title in titles:
                with self.subTest(channel=channel,topic=title):
                    self.assertIsNotNone(topic_duplicate_reason(channel,title))

    def test_new_unique_topics_are_not_flagged(self):
        for channel,topic in [
            ("legends","Отражение возле старого киоска"),
            ("science","Почему у галактик встречаются кольцевые структуры"),
            ("cinema","Рецензия на фильм 12 разгневанных мужчин 1957 года")]:
            self.assertIsNone(topic_duplicate_reason(channel,topic))

    def test_near_duplicate_titles_rejected(self):
        existing=["Архив неисправных часов на вокзале"]
        self.assertIsNotNone(topic_duplicate_reason(
            "legends","Архив неисправных часов на старом вокзале",existing))

    def test_writer_rejects_duplicates_before_provider(self):
        def never(*_):
            raise AssertionError("No model should be called for a repeated topic")
        with self.assertRaisesRegex(ValueError,"Repeated editorial topic"):
            write_article("legends","Последняя электричка из ночного депо",never)

    def test_nine_queue_refuses_duplicate_even_if_plan_has_correct_count(self):
        tasks=[]
        for ch in ("legends","science","cinema"):
            for n in range(3):
                item={"channel":ch,"topic":f"Совершенно неизвестная история {ch} {n}"}
                if ch=="science":
                    item["research_query"]="primary literature"
                tasks.append(item)
        tasks[0]["topic"]="Последняя электричка, которой нет в расписании"
        with self.assertRaisesRegex(ValueError,"Repeated editorial topic"):
            validate_plan({"date":"2026-11-21","tasks":tasks})

if __name__=="__main__":
    unittest.main()
