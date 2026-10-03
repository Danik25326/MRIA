"""Оцінювання фото розв'язків. Модель лише пропонує; відповіді перевіряються ще й за ключем вчителя кодом."""
import base64
import io
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageOps

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM = """Ти допомагаєш вчителю математики перевіряти домашні роботи за фото зошита.
Правила:
- Спочатку дослівно перепиши, що написано, по кожній вправі, і лише потім перевіряй. Нічого не вгадуй: нерозбірливе познач нерозбірливим.
- Перевір кожен крок розв'язку сам, а не лише фінальну відповідь. Закреслене не враховується.
- Правильна відповідь після хибного кроку це помилка, а не зарахування.
- Якщо фото обрізане, розмите або вправ не видно, став legible=false.
- comment_to_pupil: українською, до 250 символів, доброзичливо; вкажи, у якій вправі й на якому кроці помилка, без повного розв'язку. Якщо помилок немає, comment_to_pupil має бути порожнім рядком.
- Оцінку став лише за критеріями вчителя. Якщо критерії не дозволяють визначити оцінку, suggested_score = null.
Відповідай ЛИШЕ одним JSON-об'єктом, без тексту навколо."""

SCHEMA = """{
  "legible": true,
  "exercises": [{"id": "123(1)", "transcription": "...", "final_answer": "x=1", "steps_ok": true, "error": ""}],
  "missing_exercises": [],
  "suggested_score": 10,
  "comment_to_pupil": "",
  "confidence": "high|medium|low",
  "notes_for_teacher": ""
}"""


def norm_id(s):
    return re.sub(r"\s+", "", str(s)).lower()


def norm_ans(s):
    s = re.sub(r"[−–—]", "-", str(s).lower()).replace(",", ".")
    s = re.sub(r"\s+", "", s)
    return re.sub(r"^[a-zа-я]=", "", s)


def load_key(keys_dir, homework_id):
    p = Path(keys_dir) / f"{homework_id}.md"
    if not p.exists():
        return None, {}
    text = p.read_text(encoding="utf-8")
    answers = {}
    for line in text.splitlines():
        m = re.match(r"\s*([\w()\-.,]+)\s*:\s*(.+)", line)
        if m:
            answers[norm_id(m[1])] = norm_ans(m[2])
    return text, answers


def encode(path, max_side=1800):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode()


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("модель не повернула JSON")
    return json.loads(m.group(0))


def check(res, key_answers, has_key):
    """Незалежна перевірка: код порівнює відповіді з ключем, не довіряючи оцінці моделі. Будь-який прапорець = лише ручне рішення."""
    flags = []
    if not has_key:
        flags.append("немає ключа відповідей")
    if not res.get("legible"):
        flags.append("нерозбірливо")
    if res.get("confidence") != "high":
        flags.append(f"впевненість моделі: {res.get('confidence')}")
    if res.get("missing_exercises"):
        flags.append("не знайдено вправи: " + ", ".join(map(str, res["missing_exercises"])))
    for ex in res.get("exercises", []):
        k = norm_id(ex.get("id", ""))
        if not has_key:
            continue
        if k not in key_answers:
            flags.append(f"вправи {ex.get('id')} немає в ключі")
            continue
        match = norm_ans(ex.get("final_answer", "")) == key_answers[k]
        ex["matches_key"] = match
        if not match and ex.get("steps_ok"):
            flags.append(f"{ex.get('id')}: відповідь ≠ ключ, хоча модель вважає кроки вірними (перевір ключ або розпізнавання)")
        if match and not ex.get("steps_ok"):
            flags.append(f"{ex.get('id')}: відповідь збігається, але модель бачить помилку в кроках")
    sc = res.get("suggested_score")
    if not (isinstance(sc, int) and 1 <= sc <= 12):
        res["suggested_score"] = None
        flags.append("модель не дала оцінку 1-12")
    res["flags"] = flags
    return res


def grade(item, rubric, key_text, key_answers, model=None):
    import anthropic
    client = anthropic.Anthropic()
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": encode(p)}} for p in item["images"]]
    content.append({"type": "text", "text": (
        f"Завдання:\n{item['homework_text']}\n\n"
        f"Ключ відповідей вчителя:\n{key_text or '(немає)'}\n\n"
        f"Критерії оцінювання вчителя:\n{rubric}\n\n"
        f"Формат відповіді (JSON):\n{SCHEMA}")})
    resp = client.messages.create(
        model=model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=3000, system=SYSTEM,
        messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    return check(parse_json(text), key_answers, bool(key_text))
