# Plan: Modell für Objekterkennung mit offenem Vokabular

Ich empfehle **YOLOE-11 im Text-Prompt-Modus** (`yoloe-11s-seg.pt`, später `-m`) mit einer festen, kuratierten Klassenliste, die gecacht wird. Er läuft mit dem schon gepinnten Ultralytics 8.3.253, ohne neues Basis-Image. Fallback: **YOLOE-11s-pf** (prompt-free, 4.585 Begriffe) zum Entdecken, und Grounding-DINO-tiny als langsame Nachprüfung im Einzelfall.

Kennzeichnung: **[V]** habe ich selbst geprüft, **[D]** stammt aus der Ultralytics-Doku, **[A]** ist meine Annahme.

## Optionen im Vergleich

| Option | Läuft im Image | Vokabular | VRAM / Tempo | Lizenz |
|---|---|---|---|---|
| **YOLOE-11 s/m/l, Text-Prompt** | **ja [V]**: 8.3.253 enthält `YOLOE`, `set_classes`, `get_text_pe` und `cfg/models/11/yoloe-11*.yaml` | frei wählbare Begriffe; Textencoder `mobileclip_blt.ts` [D] | s/m ≈ 0,3–0,6 GB [A]; Laufzeit steigt mit der Zahl der Klassen (+19 % bei 1.203, +89 % bei 4.585, gemessen auf CPU [D]) | AGPL-3.0 wie YOLO11 [A]; MobileCLIP hat eine Apple-Lizenz [A] |
| YOLOE-11-pf (prompt-free) | ja [V] | 4.585 Begriffe aus der RAM-Liste: `key`, `carpet`, `fork`, `spoon`, `table`, `chair`, `remote`, `phone`, `glasses` sind dabei, **`rug` und `wallet` fehlen** [V] | wie oben, aber feste Kosten für 4.585 Klassen | wie oben |
| YOLOE-26 (n…x) | **nein**, braucht ultralytics ≥ 8.4.0 [D]; Textencoder `mobileclip2_b.ts` mit 254 MB [D] | wie oben, LVIS-AP etwas höher (26s: 30,8) [D] | ähnlich | AGPL [A] |
| YOLO-World v2 | ja [V] (`yolov8-worldv2.yaml`) | frei wählbar | ähnlich | AGPL; laut Doku etwa 3,5 AP schlechter und langsamer als YOLOE [D] |
| Detektor mit fester Klassenliste (LVIS bzw. Objects365) | ja | LVIS: `key`, `fork`, `spoon`, `wallet` (kein `carpet`/`rug`) [V]; O365: `Key`, `Carpet`, `Fork`, `Spoon`, `Remote`, `Wallet/Purse` [V] | gering | fertige Gewichte meist nicht von Ultralytics; selbst trainieren (L) |
| OWLv2 / Grounding-DINO | ja (transformers) [A] | frei wählbar, bei kleinen Objekten am besten | 1–2 GB, 150–400 ms pro Bild [A], nicht für Echtzeit | Apache-2.0 [A] |

**VRAM [V]:** Aktuell sind 1.822 von 6.144 MiB belegt (Worker-Build läuft, ohne Inferenz). Laut `worker/README.md:76` braucht die Last mit 16 Kameras etwa 1,9 GB. Für YOLOE-11s/m plus Textencoder bleiben also 3 GB und mehr übrig. Den Textencoder nach `set_classes` wieder entladen, bzw. Embeddings aus der NPZ laden.

## Warum dieses Modell

- Kein Wechsel von Ultralytics nötig. 8.4 würde auch den produktiven YOLO11-Detektor in `worker/vg_worker/detector.py` betreffen.
- Mit Text-Prompt bestimmen wir das Vokabular selbst, also auch `rug`, `wallet`, `key ring` und Synonyme, die auf eine Klasse abgebildet werden.
- Eine kuratierte Liste mit etwa 150–300 Begriffen hält die Laufzeit kalkulierbar. Die volle pf-Liste würde jede Klasse bezahlen, auch unsinnige.
- Der Prompt-free-Fallback hilft, die Liste anhand echter Szenen zu erweitern.
- Grounding-DINO kommt nur bei Bedarf zum Einsatz, z. B. wenn ein Agent fragt „Wo ist mein Schlüssel?“ und kein Treffer da ist: dann einmalig ein Standbild mit hoher Auflösung prüfen.

**Kleine Objekte:** Ein Schlüssel bei 2K (2560×1440) heruntergerechnet auf 640 hat nur noch etwa 4–8 px. Das funktioniert nicht [A]. Nötig sind Kacheln, z. B. 2×2 oder 3×2 Kacheln à 640–960 mit 15 % Überlappung, plus eine Gesamtansicht. Entweder über SAHI (eigenes Paket, nicht gepinnt) oder als eigenes Kacheln im Batch. Eigenes Kacheln ist schlanker, weil `detect()` ohnehin Batches verarbeitet (`detector.py:75`). Gegenstände bewegen sich selten, daher reicht eine **niedrige Frequenz von 0,2–1 Hz**, getrennt von den 5 fps für Personen und Fahrzeuge.

## Betroffene Dateien

- `worker/requirements.txt`: nichts ändern. Bei SAHI `sahi==<pin>` ergänzen, nur wenn eigenes Kacheln nicht reicht.
- `worker/vg_worker/settings.py` (neben Zeile 53–57): `VG_OBJECT_MODEL=yoloe-11s-seg.pt`, `VG_OBJECT_IMGSZ=960`, `VG_OBJECT_INTERVAL_S=2`, `VG_OBJECT_TILES=2x2`, `VG_OBJECT_MIN_CONF=0.25`, `VG_OBJECT_VOCAB` (Pfad zu einer Liste im Volume `models`).
- Neu: `worker/vg_worker/objects.py`, Klasse `ObjectDetector`. Lädt lazy, führt `set_classes(vocab)` aus, speichert/lädt die NPZ unter `/models/yoloe/`, kachelt, führt per NMS zusammen und behandelt Fehler isoliert, wie die Modelle in `identify.py`.
- `worker/vg_worker/pipeline.py`: eigener langsamer Pfad nur für Kameras mit `object_detection: true`. Frames verwerfen statt queuen.
- `vg/internal/system.models.objects` liefert den Status. Schema und Topics legen die Nachbar-Agenten fest.

## Machbarkeitstest (vor der Integration)

Ein eigenständiges Skript, z. B. `worker/vg_worker/objtest.py`, nach dem Muster von `selftest.py`, Aufruf mit `docker compose run --rm --no-deps worker ...` **nach** dem laufenden Build:

1. Szene vor der 2K-Testkamera aufbauen: Schlüsselbund auf Tisch und Teppich, Gabel, Löffel, Fernbedienung, Geldbörse, Brille. Abstand 1,5 m, 3 m und 5 m. (S)
2. Varianten vergleichen: YOLOE-11s und 11m, jeweils Text (Liste mit etwa 200 Begriffen) und pf. Auflösungen: 640 ganzes Bild, 1280 ganzes Bild, 2×2-Kacheln à 960, 3×2-Kacheln à 640. (M)
3. Messen: Recall pro Gegenstand und Abstand (manuell an 30–50 Bildern), Fehlalarme pro Minute, ms pro Durchlauf, VRAM-Spitze (`torch.cuda.max_memory_allocated`), Einfluss auf den Selbsttest mit 16 Kameras (`AGENTS.md`, Lasttest). (M)
4. Optional: Grounding-DINO-tiny auf denselben Bildern als Referenz für die Obergrenze. (S)
5. Abbruchkriterium: Schlüssel bei 3 m in mindestens 60 % der Bilder erkannt, und der Lasttest bleibt bei 16 × 5 fps. Wird das verfehlt, auf 11m oder 3×2-Kacheln gehen, oder Schlüssel nur auf Nachfrage per Grounding-DINO suchen. (S)

## Schritte und Aufwand

1. Machbarkeitstest wie oben. (M)
2. Vokabular-Datei mit Synonym-Abbildung (`rug` → `carpet`, deutsche Anzeigenamen für die UI). (S)
3. `objects.py` mit Kacheln, NPZ-Cache und Lazy-Load. (M)
4. Pipeline-Anbindung nur für ausgewählte Kameras, niedrige Frequenz. (M)
5. Lasttest und Abschätzung von VRAM und Zeit für weitere Kameras. (S)

## Risiken und offene Fragen

- **Kleine Objekte bleiben das Hauptrisiko.** Ohne Kacheln ist ein Schlüssel praktisch nicht zu sehen. Mit Kacheln vervielfacht sich die Rechenzeit pro Durchlauf.
- **Fehlalarme mit offenem Vokabular** (Löffel/Gabel, Teppich/Boden). Pro Klasse eigene Schwellen und „mindestens N Treffer in M Durchläufen“ verlangen.
- **Textencoder beim ersten Mal:** `set_classes` installiert `ultralytics/CLIP` von GitHub [D]. Ob das in 8.3.253 auch so ist, habe ich nicht geprüft. Gehört als eigene Schicht ins Dockerfile, nicht als Download zur Laufzeit.
- **Lizenzen:** AGPL wie bisher; die MobileCLIP-Lizenz für kommerzielle Nutzung noch prüfen [A].
- **Upgrade auf 8.4 / YOLOE-26:** etwas genauer, aber erst als eigenes Upgrade mit Regressionstest des YOLO11-Pfads.
- **Offen:** Welche Gegenstände sind wirklich wichtig? Eine Prioritätsliste bestimmt das Vokabular und die Schwellen.

Quellen: [Ultralytics YOLOE-Doku](https://docs.ultralytics.com/models/yoloe), [YOLOE ICCV-2025 Supplement](https://openaccess.thecvf.com/content/ICCV2025/supplemental/Wang_YOLOE_Real-Time_Seeing_ICCV_2025_supplemental.pdf), [RAM-Begriffsliste (THU-MIG/yoloe)](https://raw.githubusercontent.com/THU-MIG/yoloe/main/tools/ram_tag_list.txt). Die heruntergeladenen Dateien zur Prüfung liegen unter `/tmp/claude-1000/-home-miefda-vision-guard/ab5ac824-8633-4e0f-ab8f-650fdcda7d21/scratchpad/whl/` und `/tmp/claude-1000/-home-miefda-vision-guard/ab5ac824-8633-4e0f-ab8f-650fdcda7d21/scratchpad/vocab/ram.txt`.