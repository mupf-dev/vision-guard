"""Test-Werkzeug (C3-Abnahme): künstliche Tierbesuche für das Haustier-Tagebuch anlegen bzw. wieder entfernen.

Nur für Test-Stacks gedacht. Angelegte Zeilen tragen `event_id = "seed"` und lassen sich gezielt löschen.

    docker compose exec backend python -m app.pet_diary_seed --identity 7 --camera 1 --zone napf \
        --purpose food --days 14 --per-day 4-6 --skip-day 1
    docker compose exec backend python -m app.pet_diary_seed --clear

`--skip-day N` lässt den Tag N Tage vor heute ohne Besuche dieses Zwecks (1 = gestern), `--today N` legt N Besuche
heute vor der aktuellen Uhrzeit an. Die stündliche Prüfung bzw. der Abschluss des Vortags (nach einem Backend-Neustart
spätestens nach 30 s) meldet dann die Abweichung.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from .config import config
from .db import SessionLocal
from .models import PetVisit

SEED = "seed"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clear", action="store_true", help="alle Seed-Besuche löschen und beenden")
    ap.add_argument("--identity", type=int, default=None, help="Tier-Identität (leer = unbekanntes Tier)")
    ap.add_argument("--label", default="cat")
    ap.add_argument("--camera", type=int, default=1)
    ap.add_argument("--zone", default="napf", help="Zonen-ID")
    ap.add_argument("--purpose", default="food", choices=("food", "water", "litter"))
    ap.add_argument("--days", type=int, default=14, help="so viele Tage vor heute füllen")
    ap.add_argument("--per-day", default="4-6", help="Besuche je Tag, z. B. 4-6")
    ap.add_argument("--skip-day", type=int, action="append", default=[], help="Tag ohne Besuche (1 = gestern)")
    ap.add_argument("--today", type=int, default=0, help="zusätzlich so viele Besuche heute (vor jetzt)")
    ap.add_argument("--rng", type=int, default=42)
    a = ap.parse_args()

    with SessionLocal() as s:
        if a.clear:
            n = s.execute(delete(PetVisit).where(PetVisit.event_id == SEED)).rowcount
            s.commit()
            print(f"{n} Seed-Besuche gelöscht")
            return
        rnd = random.Random(a.rng)
        lo, _, hi = a.per_day.partition("-")
        lo, hi = int(lo), int(hi or lo)
        today = datetime.now(config.tz).date()
        n = 0
        for back in range(a.days, 0, -1):
            if back in a.skip_day:
                continue
            d = today - timedelta(days=back)
            for _ in range(rnd.randint(lo, hi)):
                start = datetime(d.year, d.month, d.day, tzinfo=config.tz) + timedelta(
                    hours=rnd.uniform(6.0, 22.0))
                dur = rnd.uniform(8, 90)
                s.add(PetVisit(identity_id=a.identity, label=a.label, camera_id=a.camera, zone_id=a.zone,
                               purpose=a.purpose, event_id=SEED, start_ts=start.astimezone(timezone.utc),
                               end_ts=(start + timedelta(seconds=dur)).astimezone(timezone.utc),
                               duration_s=round(dur, 1)))
                n += 1
        now = datetime.now(config.tz)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(a.today):
            start = midnight + (now - midnight) * (i + 0.5) / max(a.today, 1)
            s.add(PetVisit(identity_id=a.identity, label=a.label, camera_id=a.camera, zone_id=a.zone,
                           purpose=a.purpose, event_id=SEED, start_ts=start.astimezone(timezone.utc),
                           end_ts=(start + timedelta(seconds=20)).astimezone(timezone.utc), duration_s=20.0))
            n += 1
        s.commit()
    print(f"{n} Seed-Besuche angelegt ({a.purpose}, {a.days} Tage, ausgelassen: {a.skip_day or '–'})")


if __name__ == "__main__":
    main()
