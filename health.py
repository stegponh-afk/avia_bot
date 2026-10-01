"""
Проверка живости для Docker HEALTHCHECK.

Контейнер здоров, пока сборщик цен продолжает ходить по источникам.
Опрос раз в POLL_EVERY_MIN минут, три пропущенных подряд считаем поломкой.
"""
import sys
from datetime import datetime

import config as C
import db


def main():
    last = db.meta_get("last_poll")
    if not last:
        return 0          # ещё не было первого прохода, это не болезнь

    age = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
    limit = C.POLL_EVERY_MIN * 3 + 5
    if age > limit:
        print(f"последний опрос {age:.0f} мин назад, порог {limit}")
        return 1
    print(f"опрос {age:.0f} мин назад — норма")
    return 0


if __name__ == "__main__":
    sys.exit(main())
