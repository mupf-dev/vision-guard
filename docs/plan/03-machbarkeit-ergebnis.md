# Ergebnis: Machbarkeitstest YOLOE-11 an der Kamera „Wohnzimmer“ (cam_3)

Stand 24.10.2026. Ich habe den Test mit `worker/vg_worker/objtest.py` im Test-Image `vision-guard-worker:objtest`
ausgeführt. Der laufende Worker blieb unberührt.

**Testaufbau**
- Kamera: `cam_3`, 2880×1616, Standbilder über go2rtc `/api/frame.jpeg`.
- Szene: rundes Beistelltischchen mit Leuchte (sehr hell, überstrahlt), weißem Buch, weißem Smartphone auf dem Buch und
  Fernbedienung. Die Fernbedienung liegt größtenteils **verdeckt** hinter dem Handy, nur ein schmaler dunkler Streifen
  ist sichtbar. Dazu kommen Topfpflanze, Sofa mit Kissen, Sessel, Teppich sowie eine Person und ein Hund.
- Durchlauf: 6 Bilder im Abstand von 3 s, je Bild und Variante 3 Durchläufe für die Zeitmessung.
- Hardware: RTX A1000 6 GB, fp16. Parallel lief der produktive Worker; vor dem Test waren 3,1 GB VRAM belegt.
- Vokabular: die 50 Standard-Gegenstände mit 65 Prompts, im Test 4 Prompt-Varianten mehr (69).

## 1. Laufzeit-Nachladen von CLIP: ja, behoben

`ultralytics/nn/text_model.py` (8.3.253) macht beim Import `import clip`. Fehlt das Paket, ruft es
`check_requirements("git+https://github.com/ultralytics/CLIP.git")` auf. Im Image gibt es kein `git`, deshalb schlägt
das fehl und YOLOE mit Text-Prompts ist ohne Zusatzschicht **nicht nutzbar**.

**Lösung in `worker/Dockerfile`:** eine eigene Schicht mit
`ftfy==6.3.1`, `regex==2024.11.6` und `clip` als Tarball, gepinnt auf den Commit `7ffa84b3…` von `ultralytics/CLIP`.
Sie wird mit `--no-deps` installiert, damit torch unangetastet bleibt. `worker/requirements.txt` bleibt unverändert.

Der Textencoder `mobileclip_blt.ts` ist **600 MB** groß. Er wird beim ersten `get_text_pe` einmalig in das CWD
geladen, deshalb muss vorher `chdir` nach `/models/yoloe` erfolgen. Er liegt danach im Volume und gehört nicht ins Image.
- Erster Start (Download plus Kodierung auf der CPU): 142 s.
- Neue Prompt-Liste mit vorhandenem Encoder: 2–15 s auf der CPU.
- Liste aus dem Cache `pe_*.pt`: unter 1 s.

**Empfehlung:** Die Text-Embeddings **auf der CPU** berechnen und cachen (`model.model.cpu()` vor `get_text_pe`), erst
danach auf die GPU schieben. So kommt der 600-MB-Encoder nie in den VRAM. `objtest.py` macht das bereits so.

## 2. Laufzeit und VRAM

Die Zeiten sind Mediane in ms pro Durchlauf für das ganze Bild inklusive Kachelung und NMS. Der VRAM ist die
torch-Spitze, ohne CUDA-Kontext (im Worker wird der Kontext mitgenutzt).

| Variante | 11s ms | 11s VRAM | 11m ms | 11m VRAM |
|---|---:|---:|---:|---:|
| Ganzbild 640 | 14 | 105 MiB | 17 | 170 MiB |
| **Ganzbild 1280** | 28 | 256 MiB | **47** | **327 MiB** |
| 2×2 Kacheln à 960 | 55 | 266 MiB | 104 | 485 MiB |
| 3×2 Kacheln à 640 | 57 | 263 MiB | 106 | 482 MiB |
| Ganzbild 1280 + 2×2/960 | 89 | 269 MiB | 157 | 490 MiB |
| Ganzbild 960 + 3×2/640 | 78 | 253 MiB | 140 | 470 MiB |

Gewichte: 11s 27 MB, 11m ca. 60 MB. Selbst die teuerste Variante belegt bei einem Durchlauf alle 3–5 s weniger als
5 % der GPU-Zeit. VRAM ist mit unter 0,5 GB unkritisch (frei waren rund 3 GB).

## 3. Erkennung (6 Bilder, Score = Mittelwert über die Bilder)

| Gegenstand (Wahrheit) | 11s Ganzbild 1280 | 11m Ganzbild 1280 | 11m 2×2/960 | Bemerkung |
|---|---|---|---|---|
| Lampe | 6/6, 0,56 | **6/6, 0,82** | 6/6, 0,92 | Box korrekt, trotz Überstrahlung |
| Buch (weiß, groß) | 0,21 als `book` | **0,56** als `book` | 0,20 (in Kacheln zerschnitten) | Box korrekt [0,43–0,61 × 0,40–0,50] |
| Smartphone (weiß, auf dem Buch) | als `book` 0,54 / `tablet` 0,48 | `tablet` 0,77, `book` 0,54, `phone` 0,52 | `tablet` 0,83 | **Verwechslung Handy/Tablet/Buch** |
| Fernbedienung (fast ganz verdeckt) | `phone` 0,19, `remote` ≤ 0,08 | `remote` ≤ 0,18 | ≤ 0,15 | richtige Stelle, aber Score viel zu niedrig |
| Tischchen | 6/6, 0,62 | 6/6, 0,66 | 6/6, 0,54 | Box korrekt; in Kacheln zum Teil als `chair` gemeldet |
| Topfpflanze | 0,78 | 0,82 | 0,72 | korrekt |
| Kissen / Sofa / Teppich | 0,49 / 0,29 / 0,26 | 0,69 / 0,75 / 0,51 | 0,50 / 0,84 / 0,37 | korrekt; Sofa wird auch als `bed` gemeldet (0,5) |

**Fehlalarme** (Score ≥ 0,15):
- `bed` auf dem Sofa (0,5, alle Varianten).
- `chair` auf den Tischbeinen, nur bei Kacheln.
- `umbrella` auf dem braunen Sessel (11s mit Kacheln, 0,3–0,56).
- `mug` auf dem Pflanzentopf (0,1–0,29).
- `laptop` (11s, 0,1–0,2; 11m bei 640: 0,60).
- `charger` am Kabelsalat (0,15), `blanket` (0,15).

Prompt-Varianten wie `tv remote`, `black remote control`, `white smartphone` und `white book` brachten **keinen**
Gewinn. `tv remote` traf sogar eine falsche Stelle.

**Kachelung:** In dieser Szene (Abstand rund 1,5–2 m, Gegenstände mit 100–500 px) bringt sie **keinen Gewinn**.
Kacheln zerschneiden große Objekte (Buch, Tisch, Sofa) an den Kanten und erzeugen Teilboxen und Fehlklassen.
In den Kombi-Varianten verwirft `objtest.py` deshalb Kachelboxen, die eine innere Kachelkante berühren. Damit
entspricht das Ergebnis im Wesentlichen dem Ganzbild 1280, kostet aber die doppelte bis dreifache Zeit. Die Fernbedienung
wird auch mit Kacheln nicht zuverlässig erkannt, weil sie verdeckt ist und nicht zu klein.

## 4. Empfehlung für die Defaults

- **Modell: `yoloe-11m-seg.pt`.** Es ist deutlich robuster als 11s: Lampe 0,82 statt 0,56, Buch 0,56 statt 0,21, Sofa
  und Kissen ebenso. 47 ms bei 1280 sind bei niedriger Frequenz unkritisch. Der aktuelle Default
  `VG_INVENTORY_MODEL=yoloe-11s-seg.pt` in `worker/vg_worker/settings.py` sollte auf 11m geändert werden. 11s eignet sich
  höchstens bei vielen Inventar-Kameras.
- **imgsz 1280, Ganzbild.**
- **Kachelung standardmäßig aus.**
  - `inventory_tiling` sollte den Default `false` bekommen. Heute ist es in DB/Vertrag `true` und im Worker
    `VG_INVENTORY_TILES=2x2`.
  - Ist sie an, dann als Ganzbild plus Kacheln mit Kanten-Filter. Kachelboxen, die eine innere Kachelkante berühren,
    verwerfen, sonst entstehen zerschnittene Objekte.
  - Empfohlene Kombination: 3×2 à 640 bzw. 2×2 à 960, nur für kleine Dinge (Schlüssel, Brille), die weiter als 3 m weg
    sind. Hier nicht verifiziert, weil kein solcher Gegenstand in der Szene lag.
- **Intervall 3–5 s.** Der aktuelle Default von 3 s passt. Gegenstände bewegen sich selten, und die Bestätigung über
  `inventory_confirm_n=2` und `inventory_miss_m=3` gleicht einzelne Aussetzer aus.
- **Untergrenze `predict(conf=0.05)`**, die Auswertung erfolgt dann mit `min_score` je Label.
  - Globaler `inventory_min_score`: 0,35 passt.
  - Vorschläge für `min_score` im Vokabular:

    | Label | min_score |
    |---|---|
    | lamp, potted_plant, sofa, pillow | 0,40 |
    | table, book, rug | 0,35 |
    | phone, tablet | 0,40 |
    | bed | 0,55 (Sofa-Verwechslung) |
    | chair | 0,45 |
    | umbrella, laptop | 0,45 |
    | mug | 0,35 |
    | charger, blanket | 0,30 |
    | remote, keys, eyeglasses, wallet (klein/flach) | 0,20 |

    Für die kleinen, flachen Gegenstände (remote, keys, eyeglasses, wallet) gilt das nur zusammen mit
    `confirm_n ≥ 3`, sonst flackern Fehlalarme.
- **Label-übergreifende Unterdrückung:** Bei Boxen mit IoU > 0,7 zwischen verwechselbaren Labels nur das mit dem
  höchsten Score behalten. Gruppen: `phone`/`tablet`/`book`/`laptop`, `sofa`/`bed`, `table`/`chair`, `mug`/`glass`/`bottle`.
  Sonst taucht das Handy dreimal im Inventar auf.
- **Fernbedienung:** Verdeckt ist sie mit YOLOE nicht verlässlich zu finden. Für die MCP-Frage „Wo ist die
  Fernbedienung?“ sollte das System neben den Treffern über der Schwelle auch Kandidaten unter der Schwelle (≥ 0,08) als
  „vielleicht“ mit Bildausschnitt nennen. Die Grounding-DINO-Nachprüfung auf Nachfrage bleibt eine Option.

## 5. Offene Punkte

- Mit sichtbarer, unverdeckter Fernbedienung und mit Schlüssel und Brille in 3 m und 5 m Abstand wiederholen.
  Erst das zeigt, ob Kacheln für kleine Objekte nötig sind.
- Abends bzw. nachts testen (IR, Lampe aus). Ein Lasttest mit 16 Kameras plus Inventar steht noch aus, gemessen
  wurde nur der Inventar-Pfad allein.
- Die Seg-Variante berechnet Masken mit, die wir nicht brauchen. Eine reine Detect-Variante gibt es für YOLOE-11
  nicht als fertige Gewichte. Der Overhead ist bei dieser Frequenz unerheblich.

## Reproduzieren

```bash
docker build -t vision-guard-worker:objtest worker/
docker run --rm --gpus all --network vision-guard_default -v vision-guard_models:/models \
  -v $PWD/worker/vg_worker/objtest.py:/app/vg_worker/objtest.py:ro -v /tmp/objtest:/out \
  vision-guard-worker:objtest python -m vg_worker.objtest --models s,m --frames 6 --interval 3 \
  --focus remote,phone,book,lamp --out /out
# Nach dem nächsten Compose-Build geht es auch so:
# docker compose run --rm --no-deps worker python -m vg_worker.objtest ...
```

Die annotierten Bilder enthalten Personen und gehören daher **nicht** ins Repo. Sie liegen nur lokal unter `/out`
bzw. im Scratchpad.
