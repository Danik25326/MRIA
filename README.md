# mriia-grader

Фото роботи в Мрії → модель пропонує оцінку й коментар → **ти підтверджуєш** → бот вносить оцінку.
Автоматичного виставлення без твого підтвердження тут немає. Це свідомо, поки не виміряна точність (`stats`).

## Підготовка
1. `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\Scripts\activate`), `pip install -r requirements.txt`
2. `cp .env.example .env` і заповни.
   `MRIIA_COOKIE`: у залогіненій Мрії DevTools → Network → будь-який запит до school.mriia.gov.ua → Request Headers → `Cookie` → значення лише в `.env`.
   Це ключ до журналу: нікуди не надсилай. Коли сесія спливе, онови.
3. `cp rubric.example.md rubric.md` і задай **свої** критерії 12-бальної шкали (без файлу `grade` не запуститься).
4. Для завдання зроби `keys/<homeworkId>.md` з рядками `123(1): x=1` (див. `keys/README.md`). Без ключа кожна робота отримує прапорець.

## Порядок
```
python -m mriia_grader probe "<URL сторінки HomeworkReview однієї роботи>"   # один раз: перевіряє парсинг сторінки
python -m mriia_grader fetch "<URL сторінки «Домашня робота»>" [--homework-id N]
python -m mriia_grader grade
python -m mriia_grader review        # ти приймаєш / міняєш оцінку й коментар
python -m mriia_grader push          # сухий прогін: лише друкує
python -m mriia_grader push --yes    # справді надсилає
python -m mriia_grader undo <score_id>
python -m mriia_grader stats         # збіг моделі з твоїми оцінками
```

## Що перевірено, що ні
Перевірено тестами (`python -m unittest tests.test_parsers`) на розмітці з твого трафіку: розбір клітинок статусу, JSON-модель вікна оцінки (шкала 1→33 … 12→47 читається щоразу, не зашита), набір з 15 полів запиту `POST /api/v2/scores/complex/{pupilId}`, видалення `DELETE /api/v2/scores/{id}`.

**Не перевірено, бо код не має доступу до Мрії:**
- Пошук посилань на фото й `classLessonScoreGroupID` на сторінці HomeworkReview. Патерни виведені з Network-логу. Якщо `probe` пише «НЕ ЗНАЙДЕНО», зміни `S3_RE`/`GROUP_RE` у `mriia_grader/mriia.py`.
- Чи бачать учні оцінку «олівчиком» (`PUSH_AS_DRAFT=true`): перевір на одному учні, потім `undo`.
- Вікно редагування: Мрія може відмовити, якщо час на редагування оцінки сплив. Тоді `push` покаже тіло відповіді.

## Безпека
- Існуючі оцінки не перезаписуються: `fetch` і `push` пропускають учнів, у яких оцінка вже є.
- Фото йдуть у API моделі. Перевір правила школи щодо персональних даних дітей, на аркуші може бути прізвище.
- Це внутрішнє API Мрії, не офіційне: може змінитися, працюй лише зі своїм акаунтом і невеликими партіями.
