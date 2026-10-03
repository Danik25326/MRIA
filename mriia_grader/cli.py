"""probe → fetch → grade → review → push. Також: undo, stats."""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import grader, store
from .mriia import Mriia, download, parse_review

WORK = Path("work")


def client():
    cookie = os.getenv("MRIIA_COOKIE", "").strip()
    if not cookie:
        sys.exit("Немає MRIIA_COOKIE у .env (див. README).")
    return Mriia(cookie)


def cmd_probe(a):
    html = client().review_html(a.url)
    images, groups = parse_review(html)
    Path("debug").mkdir(exist_ok=True)
    Path("debug/review.html").write_text(html, encoding="utf-8")
    print(f"HTML: {len(html)} байт → debug/review.html (там дані учня, не публікуй)")
    print(f"Фото S3: {len(images)}")
    for u in images:
        print("  ", u.split("?")[0])  # без підпису в query
    print("classLessonScoreGroupID:", groups or "НЕ ЗНАЙДЕНО")


def cmd_fetch(a):
    c = client()
    subs = [s for s in c.submissions(a.page_url) if s.status.startswith("Здано")]
    if a.homework_id:
        subs = [s for s in subs if s.homework_id == a.homework_id]
    print(f"Здано, ще без оцінки (за статусом): {len(subs)}")
    for s in subs:
        if store.exists(s.homework_id, s.pupil_id):
            continue
        images, groups = c.review(s.review_url)
        if len(groups) != 1 or not images:
            print(f"! hw {s.homework_id} pupil {s.pupil_id}: груп={groups}, фото={len(images)}. Запусти probe.")
            continue
        modal = c.modal(int(groups[0]), s.pupil_id, s.homework_id)
        if modal.already_scored:
            print(f"- hw {s.homework_id} pupil {s.pupil_id}: оцінка вже є, пропускаю")
            continue
        folder = WORK / f"{s.homework_id}_{s.pupil_id}"
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, u in enumerate(images, 1):
            dest = folder / f"{i}.img"
            download(u, dest)
            paths.append(str(dest))
        store.save({"homework_id": s.homework_id, "pupil_id": s.pupil_id, "group_id": int(groups[0]),
                    "review_url": s.review_url, "homework_text": modal.homework_text(s.homework_id),
                    "images": paths, "status": "fetched"})
        print(f"+ hw {s.homework_id} pupil {s.pupil_id}: {len(paths)} фото")


def cmd_grade(a):
    rp = Path("rubric.md")
    if not rp.exists():
        sys.exit("Немає rubric.md: скопіюй rubric.example.md і задай свої критерії.")
    rubric = rp.read_text(encoding="utf-8")
    for it in store.items("fetched"):
        key_text, key_ans = grader.load_key("keys", it["homework_id"])
        try:
            it["model"] = grader.grade(it, rubric, key_text, key_ans)
            it["status"] = "graded"
            print(f"ok hw {it['homework_id']} pupil {it['pupil_id']}: {it['model']['suggested_score']}, прапорців {len(it['model']['flags'])}")
        except Exception as e:  # одна погана робота не має валити решту
            it["error"] = str(e)
            print(f"! hw {it['homework_id']} pupil {it['pupil_id']}: {e}")
        store.save(it)


def cmd_review(a):
    todo = store.items("graded")
    if not todo:
        return print("Нічого перевіряти.")
    for n, it in enumerate(todo, 1):
        r = it["model"]
        print(f"\n=== {n}/{len(todo)}  hw {it['homework_id']}  pupil {it['pupil_id']}")
        print("Відкрити:", it["review_url"])
        for ex in r.get("exercises", []):
            print(f"  {ex.get('id')}: «{str(ex.get('transcription', ''))[:140]}» → {ex.get('final_answer')}"
                  f" | ключ збігся: {ex.get('matches_key')} | кроки ок: {ex.get('steps_ok')} {ex.get('error', '')}")
        for f in r["flags"]:
            print("  ⚠", f)
        score, comment = r["suggested_score"], r.get("comment_to_pupil", "")
        print(f"Пропозиція: {score}   Коментар: {comment or '—'}")
        while True:
            ans = input("Enter=прийняти | 1-12=своя оцінка | c=коментар | r=відхилити | s=пропустити | q=вихід > ").strip().lower()
            if ans == "q":
                return
            if ans == "s":
                break
            if ans == "r":
                it["status"] = "rejected"
                store.save(it)
                break
            if ans == "c":
                comment = input("Коментар (≤300, порожньо = без коментаря): ").strip()
                continue
            if ans.isdigit() and 1 <= int(ans) <= 12:
                score, ans = int(ans), ""
            if ans == "":
                if score is None:
                    print("Моделі немає що запропонувати, введи число 1-12.")
                    continue
                it.update(final_score=score, final_comment=comment, status="approved")
                store.save(it)
                break


def cmd_push(a):
    c = client()
    draft = os.getenv("PUSH_AS_DRAFT", "true").lower() != "false"
    for it in store.items("approved"):
        modal = c.modal(it["group_id"], it["pupil_id"], it["homework_id"])
        tag = f"hw {it['homework_id']} pupil {it['pupil_id']}"
        if modal.already_scored:  # між review і push могли поставити вручну
            it["status"] = "skipped_scored"
            store.save(it)
            print(f"- {tag}: оцінка вже є, пропускаю")
            continue
        line = f"{tag}: оцінка {it['final_score']}, чернетка={draft}, коментар {len(it['final_comment'] or '')} симв."
        if not a.yes:
            print("[сухий прогін]", line)
            continue
        it["score_id"] = c.post_score(it["pupil_id"], modal, it["final_score"], it["final_comment"], draft, it["review_url"])
        it["status"] = "pushed"
        store.save(it)
        print("ok", line, "score_id", it["score_id"])


def cmd_undo(a):
    client().delete_score(a.score_id)
    for it in store.items("pushed"):
        if it.get("score_id") == a.score_id:
            it["status"] = "undone"
            store.save(it)
    print("Видалено", a.score_id)


def cmd_stats(a):
    done = [i for i in store.items() if i.get("final_score") is not None and i.get("model", {}).get("suggested_score") is not None]
    if not done:
        return print("Ще немає перевірених тобою робіт.")
    exact = sum(i["final_score"] == i["model"]["suggested_score"] for i in done)
    near = sum(abs(i["final_score"] - i["model"]["suggested_score"]) <= 1 for i in done)
    print(f"Робіт: {len(done)}. Збіг із твоєю оцінкою: точно {exact}, ±1 {near}.")
    clean = [i for i in done if not i["model"]["flags"]]
    if clean:
        ok = sum(i["final_score"] == i["model"]["suggested_score"] for i in clean)
        print(f"Серед робіт без прапорців: {len(clean)}, точний збіг {ok}.")


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(prog="mriia_grader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe"); p.add_argument("url"); p.set_defaults(f=cmd_probe)
    p = sub.add_parser("fetch"); p.add_argument("page_url"); p.add_argument("--homework-id", type=int); p.set_defaults(f=cmd_fetch)
    sub.add_parser("grade").set_defaults(f=cmd_grade)
    sub.add_parser("review").set_defaults(f=cmd_review)
    p = sub.add_parser("push"); p.add_argument("--yes", action="store_true", help="справді надіслати (без цього лише сухий прогін)"); p.set_defaults(f=cmd_push)
    p = sub.add_parser("undo"); p.add_argument("score_id", type=int); p.set_defaults(f=cmd_undo)
    sub.add_parser("stats").set_defaults(f=cmd_stats)
    a = ap.parse_args()
    a.f(a)
