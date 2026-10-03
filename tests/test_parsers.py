"""Перевірка парсерів на розмітці, знятій зі справжньої Мрії (клітинка), і на синтетичній моделі вікна оцінки."""
import json
import unittest

from mriia_grader import grader
from mriia_grader.mriia import parse_modal, parse_review, parse_submissions, score_body

REAL_TD = """<td class="homework-cell-status _center _eps" style="cursor: pointer" onclick="window.location.href='/Homework/HomeworkReview?homeworkId=11752135&amp;pupilId=158411&amp;classId=80517&amp;educationalPlanYearID=769519&amp;isNotRoot=False&amp;isNotDiary=False&amp;isMyLesson=False&amp;filterPeriodId=17146&amp;filterPeriodType=Semester&amp;referral=homework_page';" onmousedown="if (event.button === 1) { window.open('/Homework/HomeworkReview?homeworkId=11752135&amp;pupilId=158411','_blank'); }">
<div id="0-0" class="table-eps-icon relative"><div class="label-homework-status" style="cursor: pointer">
 На перевірку в класі
</div></div></td>"""


def cell(hw, pupil, text):
    return (f"<td class=\"homework-cell-status\" onclick=\"window.location.href='/Homework/HomeworkReview?homeworkId={hw}&amp;pupilId={pupil}&amp;classId=1';\">"
            f"<div class=\"label-homework-status\">{text}</div></td>")


def modal_html(scored=False):
    scale = dict(zip(range(1, 13), [33, 35, 36, 38, 40, 41, 42, 43, 44, 45, 46, 47]))
    data = {"ScoreValues": [{"ID": i, "Value": str(v), "ShortValue": str(v)} for v, i in scale.items()],
            "ScoreHomeworkEvaluationInfos": [{"ClassLessonHomeworkId": 111, "Homework": "Вправа 123(1-2)\r\nhttps://x", "FileStoragesCount": 2}]}
    js = json.dumps(json.dumps(data, ensure_ascii=False), ensure_ascii=False)[1:-1]
    hidden = {"ClassLessonID": 5, "ClassLessonScoreGroupID": 7, "EducationalPlanID": 9, "ClassLessonHomeworkId": 111,
              "HasDraft": "True", "HasDots": "True", "ScoreValueID": 45 if scored else ""}
    inputs = "".join(f'<input type="hidden" id="{k}" name="{k}" value="{v}" />' for k, v in hidden.items())
    return f'<form>{inputs}<input name="IsDraft" type="hidden" value="false"></form><script>var data = JSON.parse("{js}");</script>'


class Parsers(unittest.TestCase):
    def test_real_cell(self):
        (s,) = parse_submissions(f"<table><tr>{REAL_TD}</tr></table>")
        self.assertEqual((s.homework_id, s.pupil_id, s.status), (11752135, 158411, "На перевірку в класі"))
        self.assertIn("/Homework/HomeworkReview?homeworkId=11752135&pupilId=158411&classId=80517", s.review_url)

    def test_status_filter(self):
        html = "<table><tr>" + cell(1, 10, "Не здано") + cell(1, 11, "Здано - 30.09 о 19:50") + cell(1, 12, "Оцінено") + "</tr></table>"
        got = [s.pupil_id for s in parse_submissions(html) if s.status.startswith("Здано")]
        self.assertEqual(got, [11])

    def test_modal(self):
        m = parse_modal(modal_html())
        self.assertEqual(m.scale["10"], 45)
        self.assertEqual(m.scale["12"], 47)
        self.assertFalse(m.already_scored)
        self.assertTrue(m.homework_text(111).startswith("Вправа 123(1-2)\r\n"))
        self.assertTrue(parse_modal(modal_html(scored=True)).already_scored)

    def test_body_has_same_15_fields_as_real_request(self):
        real = {"classLessonHomeworkId", "classLessonId", "classLessonScoreGroupId", "comment", "defaultScoreCharacteristicId",
                "educationalPlanId", "hasDots", "hasDraft", "isDraft", "learningResultIds", "origin",
                "saveLearningResults", "scorePoints", "scoreValueId", "valueKind"}
        b = score_body(parse_modal(modal_html()), 10, "", True)
        self.assertEqual(set(b), real)
        self.assertEqual((b["scoreValueId"], b["comment"], b["isDraft"]), (45, None, True))
        self.assertEqual(len(score_body(parse_modal(modal_html()), 9, "я" * 500, False)["comment"]), 300)
        with self.assertRaises(ValueError):
            score_body(parse_modal(modal_html()), 13, "", True)

    def test_review_regex(self):  # лише юніт-тест патернів, а не доказ про справжню сторінку
        html = ('<img src="https://mriia-lms-homework.s3.eu-central-1.amazonaws.com/a/b.jpg?X-Amz-Signature=abc&amp;X-Amz-Expires=9">'
                '<script>var u="/Score/Score?classLessonScoreGroupID=123&x=1"; var m={"ClassLessonScoreGroupID":123}</script>')
        images, groups = parse_review(html)
        self.assertEqual(len(images), 1)
        self.assertIn("X-Amz-Expires=9", images[0])
        self.assertEqual(groups, ["123"])


class Checker(unittest.TestCase):
    KEY = {"123(1)": "1", "123(2)": "-3"}

    def res(self, a1="x=1", a2="x = −3", ok1=True, ok2=True, conf="high"):
        return {"legible": True, "confidence": conf, "suggested_score": 10, "missing_exercises": [],
                "exercises": [{"id": "123(1)", "final_answer": a1, "steps_ok": ok1},
                              {"id": "123 (2)", "final_answer": a2, "steps_ok": ok2}]}

    def test_clean(self):
        self.assertEqual(grader.check(self.res(), self.KEY, True)["flags"], [])

    def test_wrong_answer_vs_model_says_ok(self):
        self.assertEqual(len(grader.check(self.res(a2="x=3"), self.KEY, True)["flags"]), 1)

    def test_right_answer_bad_steps(self):
        self.assertEqual(len(grader.check(self.res(ok1=False), self.KEY, True)["flags"]), 1)

    def test_no_key_always_flagged(self):
        self.assertIn("немає ключа відповідей", grader.check(self.res(), {}, False)["flags"])

    def test_bad_score_dropped(self):
        r = self.res(); r["suggested_score"] = 15
        out = grader.check(r, self.KEY, True)
        self.assertIsNone(out["suggested_score"])

    def test_json_in_fences(self):
        self.assertEqual(grader.parse_json("```json\n{\"a\": 1}\n```"), {"a": 1})


if __name__ == "__main__":
    unittest.main()
