"""Клієнт Мрії. Ендпоінти й поля взято зі справжнього трафіку (DevTools), не з документації."""
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://school.mriia.gov.ua"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
XHR = {"X-Requested-With": "XMLHttpRequest"}

ONCLICK_RE = re.compile(r"window\.location\.href='([^']+)'")
IDS_RE = re.compile(r"homeworkId=(\d+)&(?:amp;)?pupilId=(\d+)", re.I)
DATA_RE = re.compile(r'var data = JSON\.parse\("((?:[^"\\]|\\.)*)"\)')
# ЦІ ДВА ПАТЕРНИ ВИВЕДЕНО З NETWORK-ЛОГУ, А НЕ З КОДУ СТОРІНКИ HomeworkReview. Перевіряє `probe`.
S3_RE = re.compile(r"https://mriia-lms-homework\.s3[^\"'\s<>\\]+", re.I)
GROUP_RE = re.compile(r"classLessonScoreGroupID[\"']?\s*[=:]\s*[\"']?(\d+)", re.I)


class SessionExpired(RuntimeError):
    pass


@dataclass
class Submission:
    homework_id: int
    pupil_id: int
    status: str
    review_url: str


@dataclass
class Modal:
    hidden: dict
    data: dict

    @property
    def scale(self):
        """{'1': 33, ..., '12': 47}. Береться з відповіді щоразу, не зашито."""
        return {v["Value"]: v["ID"] for v in self.data["ScoreValues"]}

    @property
    def already_scored(self):
        return bool(self.hidden.get("ScoreValueID") or self.hidden.get("ScorePoints"))

    def homework_text(self, homework_id):
        for h in self.data.get("ScoreHomeworkEvaluationInfos", []):
            if h["ClassLessonHomeworkId"] == homework_id:
                return h["Homework"]
        return ""


def parse_submissions(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for td in soup.select("td.homework-cell-status"):
        m = ONCLICK_RE.search(td.get("onclick", ""))
        label = td.select_one(".label-homework-status")
        if not m or not label:
            continue
        ids = IDS_RE.search(m.group(1))
        if not ids:
            continue
        out.append(Submission(int(ids[1]), int(ids[2]),
                              label.get_text(" ", strip=True),
                              urljoin(BASE, m.group(1).replace("&amp;", "&"))))
    return out


def parse_review(html):
    html = html.replace("&amp;", "&").replace("\\u0026", "&")
    images = list(dict.fromkeys(S3_RE.findall(html)))
    groups = sorted(set(GROUP_RE.findall(html)))
    return images, groups


def parse_modal(text):
    soup = BeautifulSoup(text, "html.parser")
    hidden = {}
    for i in soup.select("form input[type=hidden]"):
        if i.get("name"):
            hidden.setdefault(i["name"], i.get("value", ""))
    m = DATA_RE.search(text)
    if not m:
        raise RuntimeError("Немає JSON-моделі у вікні оцінки (змінилась розмітка?)")
    return Modal(hidden, json.loads(json.loads('"' + m.group(1) + '"')))


def score_body(modal, score, comment, draft):
    """Тіло POST /api/v2/scores/complex/{pupilId}: ті самі 15 полів, що в реальному запиті."""
    h = modal.hidden
    if str(score) not in modal.scale:
        raise ValueError(f"Оцінки {score} немає у шкалі {sorted(modal.scale)}")

    def flag(k):
        return str(h.get(k, "")).lower() == "true"

    return {
        "classLessonScoreGroupId": int(h["ClassLessonScoreGroupID"]),
        "classLessonId": int(h["ClassLessonID"]),
        "educationalPlanId": int(h["EducationalPlanID"]),
        "classLessonHomeworkId": int(h["ClassLessonHomeworkId"]),
        "scoreValueId": modal.scale[str(score)],
        "valueKind": 0,
        "comment": (comment or "")[:300] or None,
        "isDraft": bool(draft),
        "hasDraft": flag("HasDraft"),
        "hasDots": flag("HasDots"),
        "origin": 0,
        "saveLearningResults": True,
        "learningResultIds": [],
        "scorePoints": None,
        "defaultScoreCharacteristicId": None,
    }


class Mriia:
    def __init__(self, cookie, delay=0.3):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        for part in cookie.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                self.s.cookies.set(k, v, domain="school.mriia.gov.ua", path="/")
        self.delay = delay

    def _req(self, method, url, **kw):
        time.sleep(self.delay)  # ліміт Мрії 20 запитів/с, нам стільки не треба
        r = self.s.request(method, urljoin(BASE, url), timeout=30, **kw)
        path = urlparse(r.url).path.lower()
        if "login" in path or "account" in path:
            raise SessionExpired("Сесія недійсна: увійди в Мрію й онови MRIIA_COOKIE у .env")
        if not r.ok:
            raise RuntimeError(f"{r.status_code} {method} {url}: {r.text[:300]}")
        return r

    def submissions(self, page_url):
        return parse_submissions(self._req("GET", page_url).text)

    def review(self, url):
        return parse_review(self._req("GET", url).text)

    def review_html(self, url):
        return self._req("GET", url).text

    def modal(self, group_id, pupil_id, homework_id):
        r = self._req("GET", "/Score/Score", headers=XHR, params={
            "classLessonScoreGroupID": group_id, "pupilID": pupil_id,
            "isAutoScore": "false", "classLessonHomeworkId": homework_id,
            "source": "homework"})
        return parse_modal(r.text)

    def post_score(self, pupil_id, modal, score, comment, draft, referer=None):
        r = self._req("POST", f"/api/v2/scores/complex/{pupil_id}",
                      json=score_body(modal, score, comment, draft),
                      headers={**XHR, "Origin": BASE, "Referer": referer or BASE + "/"})
        res = r.json()
        if not res.get("isAdded"):
            raise RuntimeError(f"Мрія не підтвердила запис: {res}")
        return res["classLessonPupilScoreId"]

    def delete_score(self, score_id):
        self._req("DELETE", f"/api/v2/scores/{score_id}", headers=XHR)


def download(url, dest):
    """Підписане посилання S3: у браузері запит ішов без кукі, тому й тут без сесії."""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.write_bytes(r.content)
