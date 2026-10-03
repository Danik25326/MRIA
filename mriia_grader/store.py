"""Черга робіт: по одному JSON-файлу на (завдання, учень). Імена учнів не зберігаються."""
import json
from pathlib import Path

QUEUE = Path("queue")


def _path(homework_id, pupil_id):
    return QUEUE / f"{homework_id}_{pupil_id}.json"


def exists(homework_id, pupil_id):
    return _path(homework_id, pupil_id).exists()


def save(item):
    QUEUE.mkdir(exist_ok=True)
    _path(item["homework_id"], item["pupil_id"]).write_text(
        json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def items(status=None):
    out = []
    for f in sorted(QUEUE.glob("*.json")):
        it = json.loads(f.read_text(encoding="utf-8"))
        if status is None or it["status"] == status:
            out.append(it)
    return out
