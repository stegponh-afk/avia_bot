"""
Бэкап базы: раз в сутки сжатая копия в /data/backups, хранятся последние N.

База — это не кэш, который можно выкачать заново. Скидки считаются по
накопленной истории цен: потеряешь её — бот неделями не отличит скидку от
обычной цены. Поэтому копия делается штатным механизмом SQLite (backup API):
он снимает согласованный снимок, пока бот продолжает писать.

Восстановить (контейнер остановлен):
    gunzip -c /data/backups/avia-2026-09-30.db.gz > /data/avia.db
"""
import glob
import gzip
import os
import shutil
import sqlite3
from datetime import datetime

import config as C

DIR = os.path.join(os.path.dirname(os.path.abspath(C.DB)), "backups")


def run():
    """Снять копию. Возвращает (путь, размер в МБ). Бросает исключение при сбое."""
    os.makedirs(DIR, exist_ok=True)
    size = os.path.getsize(C.DB)
    free = shutil.disk_usage(DIR).free
    # сжатие ~в 3-4 раза, но временный несжатый снимок нужен целиком
    if free < size * 1.6:
        raise RuntimeError(f"мало места на диске: свободно {free // 2**20} МБ, "
                           f"база {size // 2**20} МБ")

    stamp = datetime.now().strftime("%Y-%m-%d")
    raw = os.path.join(DIR, f"avia-{stamp}.db")
    out = raw + ".gz"
    src = sqlite3.connect(C.DB)          # своё соединение: не мешаем боту
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with open(raw, "rb") as f, gzip.open(out, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1024 * 1024)
    os.remove(raw)

    for old in sorted(glob.glob(os.path.join(DIR, "avia-*.db.gz")))[:-C.BACKUP_KEEP]:
        os.remove(old)
    return out, round(os.path.getsize(out) / 2**20, 1)


def listing():
    return [(os.path.basename(p), round(os.path.getsize(p) / 2**20, 1))
            for p in sorted(glob.glob(os.path.join(DIR, "avia-*.db.gz")))]


if __name__ == "__main__":
    print(run())
