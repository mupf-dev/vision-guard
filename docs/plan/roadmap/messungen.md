# Messungen Welle 0 (W0-b) – Machbarkeitstests

Stand: 25.10.2026, Nacht (Zweig `w0/machbarkeit`). Alle Zahlen sind **gemessen [G]**, sofern nicht als
Schätzung [S] markiert. Hardware: RTX A1000 Laptop 6 GB (WSL2), Treiber 551.x, CUDA 12.4.
Während aller GPU-Tests lief der Haupt-Stack (`vision-guard`) unverändert weiter: Worker mit YOLO11s, Gesicht,
Kennzeichen, DINOv2-Tier und **YOLOE-11m (Inventar auf `cam_3`)**. Jeder GPU-Test lief einzeln unter
`flock ~/vg-gpu.lock` in einem eigenen Wegwerf-Container. Der Worker meldete in der ganzen Zeit keinen OOM, die
Detektion blieb bei 5,0 fps.

## Ergebnis auf einen Blick

| Thema | Test | Go/No-Go | Empfehlung in einem Satz |
|---|---|---|---|
| C1 Sturz/Liegen | `posetest` | **Go mit Auflagen** | `yolo11n-pose` (+0,1 GB) genügt. Größtes Risiko ist nicht die Pose, sondern dass der Detektor liegende Personen nur in 48 % der Frames findet: Track-Abriss im Liegen als Signal werten. |
| D1 Armgesten | `posetest` | **Go (vorläufig)** | Regelklassifikator mit höchstens 1 Fehlalarm auf 56 Negativ-Clips; Halte-Gesten mangels Material nicht gemessen → eigene Aufnahmen vor der Abnahme. |
| B4/D2 Exemplare | `itemtest` | **Bedingt Go** | DINOv2 nur für Gegenstände ab ≈ 90–120 px; Go-Kriterium AUC ≥ 0,90 bei ≥ 60 px verfehlt (0,84). Kleine Dinge (Schlüssel) → No-Go, UI-Warnung. |
| C5 Kamera-Gesundheit | `healthtest` (CPU) | **Go** | 6 ms/Frame auf der CPU; an `cam_3` alle Störungen erkannt, 0 Fehlalarme, auch nachts im IR-Betrieb. |
| B1 Zeitmaschine | `histtest` | **Go** | SigLIP 2 B/16-256 (Bild-Turm +0,29 GB, 14 ms) – deutlich besser als MobileCLIP2, Deutsch brauchbar. |
| A2 Frag die Kamera | `vlm/bench.py` | **Go nur mit GPU-Lease** | Moondream 2 INT4 läuft mit torch 2.5.1 (0,99 s/Antwort, 21/22 richtig), Spitze 5,70 GB **mit** YOLOE → YOLOE muss entladen werden. |
| A1 Flackern | `flickertest` | **No-Go für Rohzustände, Go mit Filter** | Nachts 250–300 appear/disappear pro Stunde an einer ruhigen Szene. `confirm_n` 3 / `miss_m` 10 senkt das in der Simulation um ≈ 93 %; dazu Verzögerung ≥ 120 s und Score-Hysterese. |

---

## 1. VRAM-Grundlast (Bezug für alle Budgets)

| Messung | Wert |
|---|---|
| Haupt-Worker inkl. YOLOE-11m, 1 Kamera + Inventar (NVML, Gerät gesamt) | **2,84–3,42 GB** (jeweils ohne Testprozess gemessen), schwankend. Der PyTorch-Cache des Workers wächst und schrumpft mit den YOLOE-Durchläufen. |
| eigener CUDA-Kontext eines Test-Containers | ≈ 0,1–0,25 GB (nicht trennbar unter WSL2; NVML liefert keine Werte je Prozess) |

Hinweis für alle Folgewellen: Unter **WSL2 sieht `cudaMemGetInfo` die anderen Prozesse nicht zuverlässig**
(`torch.cuda.mem_get_info()` meldete 5,1 GB frei, NVML 2,6 GB). Freien VRAM für Lease/Prüfungen deshalb immer per
NVML bestimmen (gilt für die GPU-Lease in K3/A2).

## 2. C1/D1 – Pose, Sturz/Liegen, Gesten (`posetest`)

**Aufbau.** `worker/vg_worker/tests_feasibility/posetest.py`, Gewichte `yolo11n-pose.pt` und `yolo11s-pose.pt`
(ultralytics 8.3.253, fp16). Daten:
- **Liegen:** UR Fall Detection Dataset (Kwolek & Kępski 2014, CC BY-NC-SA 4.0), 30 Stürze + 40 Alltagsszenen,
  Kamera 0, RGB-Hälfte 320×240 (ähnlich klein wie Personen im Detect-Stream). Frame-Wahrheit aus den Label-CSVs
  (2373 Frames „liegt“, 7452 „liegt nicht“).
- **Gesten:** UTD-MHAD (Chen et al. 2015, nur Forschung/privat), 8 Personen, 640×480, 15 fps, Clips ≈ 3 s.
- Detektor wie im Produkt: `yolo11s.pt` aus dem Produktiv-Volume (nur lesend).

**Last und VRAM** (zweiter Lauf, GPU sonst nur mit dem Haupt-Worker geteilt):

| Modell | Batch 16 Crops à 256 px | 1 Crop | Ganzbild 640 | torch reserviert | GPU-Anteil bei 16 Kameras × 5 fps × 1 Person |
|---|---:|---:|---:|---:|---:|
| yolo11n-pose | 27,5 ms | 8,7 ms | 9,2 ms | **100 MiB** | 14 % |
| yolo11s-pose | 35,6 ms | 10,5 ms | 10,5 ms | 186 MiB | 18 % |

Gerätespitze im ganzen Test: **3,76 GB** (< 4,5 GB). Unter Konkurrenz (erster Lauf) lagen die Batch-Zeiten bei
38/48 ms. Die Zeiten sind von ultralytics' CPU-Vor-/Nachverarbeitung dominiert (1 Crop ≈ Ganzbild).

**Liegen erkennen** (Frame-Ebene, nur Frames mit Wahrheit −1/1):

| Verfahren | Recall „liegt“ | Spezifität | Präzision | Stürze erkannt (≥ 0,5 s / ≥ 1 s liegend) | Fehlalarm Alltag (24 Clips ohne Liegen) |
|---|---:|---:|---:|---:|---:|
| Pose Ganzbild (n) | 0,40 | 0,92 | 0,86 | 20/30 / 15/30 | 4/24 |
| Pose auf Detektor-Crop (n) | 0,34 | 0,86 | 0,84 | 22/30 / 12/30 | 4/24 |
| nur Box-Seitenverhältnis > 1,3 | 0,39 | 0,89 | **1,00** | 19/30 / 11/30 | **0/24** |
| Pose Ganzbild (s) | 0,40 | 0,94 | 0,86 | 20/30 / 16/30 | 4/24 |

- **Wichtigster Befund:** Der Produktiv-Detektor findet die liegende Person nur in **60 %** der Liegend-Frames
  (conf ≥ 0,25) bzw. **48 %** bei `min_score` 0,5. Auch das Pose-Modell selbst findet in 41 % der Liegend-Frames
  niemanden. Recall-Verluste kommen also fast vollständig aus der Detektion (Person halb hinter Möbeln, ungewohnte
  Lage), nicht aus der Haltungsregel. Die Befürchtung aus `00-wellenplan.md` §6 („Ob ein liegender Track abreißt,
  ist nicht belegt“) ist damit **belegt: er reißt häufig ab**.
- Die Stürze im Datensatz enden oft < 1 s nach dem Aufprall; die Ereignisquoten sind deshalb Untergrenzen. In
  `testcam` cam3 (`sturz.mp4`) liegt die Person nach jedem Sturz 10 s still (Standbild).
- „Liegen auf Bett/Sofa“ (16 Alltagsclips) wird in 5–8 Fällen als Liegen erkannt → Ausschluss über Zonen (wie
  geplant) ist nötig.
- `s` bringt gegenüber `n` praktisch nichts.

**Gesten** (Regelklassifikator `classify_gesture`, Ganzbild-Pose):

| Geste | Positiv-Clips | erkannt in ≥ 1 Frame | ≥ 0,5 s gehalten | Fehlalarm auf 56 Negativ-Clips (Frame / 0,5 s) |
|---|---|---:|---:|---:|
| Arme verschränkt | a6 | 6/8 | 2/8 | 0 / 0 |
| Hand über Kopf | a3 Winken, a17 Aufschlag | 11/16 | 5–7/16 | 1 / 1 |
| Arm seitlich gestreckt | a1/a2 Wischen | 10–12/16 | 3/16 | 0 / 0 |

UTD-Gesten sind kurze Bewegungen, keine Haltegesten; eine 1-s-Haltezeit (Entwurf `gesture_hold_s`) lässt sich
damit nicht prüfen. Positiv: keine Fehlalarme bei Klatschen, Bizeps-Curl, Joggen, Gehen, Hinsetzen/Aufstehen;
der eine „Hand über Kopf“-Treffer stammt aus einer Kniebeuge mit gestreckten Armen.

**Go/No-Go:** C1 **Go mit Auflagen**, D1 **Go (vorläufig)**.

**Empfehlungen C1/D1**
1. Modell **`yolo11n-pose`**, Ganzbild-Pose nur auf Kameras mit `pose_enabled`; nicht jeden Frame, sondern ≤ 2,5 fps
   je Kamera oder nur wenn ein Personen-Track existiert. VRAM +0,1 GB (Plan 0,1–0,2 ✓).
2. Haltung „liegt“ = Box-Seitenverhältnis > 1,3 **oder** Rumpf-/Körperwinkel > 55°; Box allein hat keine
   Fehlalarme und dient als harte Bestätigung, Pose erhöht den Recall.
3. **Track-Abriss im Liegen** ist ein eigenes Signal: Endet ein Track, dessen letzte Haltung „liegt“ war (oder der
   in der unteren Bildhälfte schnell von stehend auf flach wechselte), bleibt der Zustand „liegt“ für
   `fall_default_dwell_s` erhalten, statt mit dem Track zu verschwinden. Zusätzlich `min_score` für `person` bei
   `pose_enabled` auf 0,3 senken (Recall 60 % statt 48 %).
4. Gesten vor der Abnahme mit **eigenen Aufnahmen** (Haltegesten 1–2 s, 2–4 m Abstand) prüfen; bis dahin
   `gesture_hold_s` 1,0 s und 80 % beibehalten.

## 3. B4/D2 – Eigene Exemplare mit DINOv2 (`itemtest`)

**Aufbau.** Eingebettet wie im Worker (`identify.PetModel`: quadratisch auffüllen, 224 px, fp16, L2), Modell
`vit_small_patch14_dinov2.lvd142m`. Es gab **keine Handyfotos** eigener Gegenstände. Ersatz:
- **Stanford Online Products** (MIT-Lizenz): Tasse 41, Wasserkocher 60, Hefter 60, Lampe 43, Stuhl 50 Produkte.
  Galerie = 2 Fotos je Produkt (≈ Anlernen per Handy), Anfrage = weitere Fotos, verkleinert auf 40–200 px mit
  Kamera-Störungen (JPEG 60, Unschärfe, Helligkeit). „Fremdes Exemplar“ = anderes Produkt derselben Kategorie.
  Das ist **strenger** als zu Hause (eBay-Fotos aus anderen Blickwinkeln, sehr ähnliche Produkte).
- **Echte Inventar-Crops von `cam_3`** (19 Gegenstände, 56 Crops, Median 759 px, nur lesend).

| Crop-Größe (längste Seite) | AUC (Galerie einfach) | AUC (Galerie + Augmentierung) | TPR bei 5 % FPR (aug) |
|---|---:|---:|---:|
| 40 px | 0,74 | 0,77 | 0,35 |
| 60 px | 0,81 | **0,84** | 0,52 |
| 90 px | 0,86 | 0,87 | 0,60 |
| 120 px | 0,88 | 0,88 | 0,66 |
| 200 px | 0,89 | 0,89 | 0,66 |
| Original | 0,89 | 0,89 | 0,69 |

Je Kategorie bei 120 px (aug): Lampe 0,93 · Hefter 0,92 · Stuhl 0,90 · Tasse 0,84 · Wasserkocher 0,80.
Schwelle bei 5 % FPR: 0,42 (Lampe) bis 0,62 (Tasse). Echte `cam_3`-Crops: gleiches Exemplar zu verschiedenen Zeiten
vs. andere Gegenstände **AUC 0,96**, Ähnlichkeit gleiches Exemplar Mittel 0,90 (5-%-Quantil 0,77).

Zeit/VRAM: **5,5 ms** je Embedding (Batch 1, fp16), 27 ms bei Batch 16; torch reserviert 128 MiB als eigener
Prozess, im Worker **0** (geteiltes Tier-Modell). Go-Kriterien Zeit ≤ 15 ms ✓, VRAM ≤ 0,2 GB ✓, **AUC ≥ 0,90 bei
≥ 60 px ✗ (0,84)**.

**Go/No-Go:** **Bedingt Go** – B4 für große Gegenstände (Taschen, Rucksäcke, Jacken, Lampen, Geräte) ab ≈ 90 px;
**No-Go** für Schlüssel/Brille/kleine Dinge (< 60 px, nicht gemessen, aber schon Tassen bei 60 px nur 0,75–0,78).

**Empfehlungen B4/D2**
1. `item_match_threshold` **0,55**; Band **0,40–0,55** → Rückfrage (D2) statt Zuordnung.
2. Galerie beim Anlernen um verkleinerte (60/120 px) und gespiegelte Fassungen ergänzen (+0,03 AUC bei 40–60 px).
3. Crops unter 90 px nicht automatisch zuordnen; UI-Warnung „Gegenstand erscheint im Bild sehr klein“.
4. Mit echten Handyfotos (Leitbeispiel Schlüssel, Brille) vor der Abnahme nachmessen:
   `itemtest --photos <ordner>/<label>/<exemplar>/*.jpg --fetch 0`.

## 4. C5 – Kamera-Gesundheit (`healthtest`, nur CPU)

**Aufbau.** Graustufen auf 320 px Breite: Helligkeit, Kontrast, normierte Schärfe (Laplace-Varianz/Kontrast²),
Anteil flacher 16×16-Blöcke, Sättigung (IR-Erkennung), Phasenkorrelation der Gradientenbilder gegen eine Referenz
(Verschiebung + Antwortstärke relativ zur gleitenden Basis der Kamera), Bilddifferenz zum Vorframe (eingefroren).
Zwei Referenzen (Tag/IR, gewählt über die Sättigung). Synthetische Störungen auf echten Frames, dazu
Live-Beobachtung `cam_3` in der Nacht. Bilder wurden nur im Speicher verarbeitet.

| Störung (synthetisch) | `cam_3` (27 Frames) | testcam cam1 (Flur, 29) | testcam cam2 (Straße, 29) |
|---|---:|---:|---:|
| verschoben 5/10/20 % | 27/27 je Stufe | 29/29 | 29/29 |
| gedreht 5°/10° | 27/27 | 29/29 | 29/29 |
| unscharf σ 3/6 | 27/27 · 27/27 | 29/29 · 29/29 | 29/29 · 5/29 (sonst als andere Störung gemeldet) |
| abgedeckt ganz / 60 % | 27/27 · 27/27 | 29/29 · 29/29 | 29/29 · 29/29 |
| eingefroren | 27/27 | 29/29 | 29/29 |
| **Fehlalarme** ungestört / Licht −45 % / Licht +35 % / IR / Rauschen | **0 / 0 / 0 / 0 / 0** | 4 / 4 / 10 / 7 / 4 (je Einzelframe „verschoben“, Personen im Bild) | 4 / 28 / 4 / 4 / 4 |

- Die Testcam-Videos sind Zusammenschnitte mit Szenenwechseln und vielen Personen; dort sind Einzelframe-Fehlalarme
  zu erwarten. Die Hysterese (Problem erst nach 60 s am Stück) filtert sie; bei cam2 meldet die stark abgedunkelte
  Straße „verdeckt“ – realistisch, wenn eine Außenkamera nachts kein IR hat.
- Rechenzeit **5,9 ms** je Frame (CPU, 320 px).
- **Live `cam_3`, Nacht (23:58–01:57, 711 Frames im 10-s-Takt, durchgehend IR):** **0 Rohbefunde, 0 Alarme**;
  kleinste Differenz zum Vorframe 0,45 (Schwelle „eingefroren“ 0,05 hat Faktor 9 Abstand), Struktur-Korrelation
  ≥ 0,87, Verschiebung ≤ 0,03 %. Ein Tag/Nacht-Wechsel lag nicht im Messfenster (Kamera war beim Start schon im IR).
  Bewertet ist damit nur „ruhige Nacht“; Lichtwechsel am Morgen bleibt für die C5-Abnahme offen.
- Beim Umschalten in den IR-Betrieb (≈ 23:55) lieferte go2rtc kurz HTTP 500 (`frame.jpeg`), der Worker-Recorder
  meldete „dimensions not set“ – C5 darf kurze Ausfälle (< 60 s) nicht als „offline“ melden.

**Go/No-Go:** **Go.**

**Empfehlungen C5**
1. Die Messfunktionen aus `healthtest.py` (`metrics`, `compare`, `classify`, `HealthState`) als Vorlage für
   `frame_metrics.py`/`health.py` übernehmen; alle 5–10 s ein Frame genügt.
2. Schwellen: `covered` = flache Blöcke +35 %-Punkte gegenüber Referenz oder Kontrast < 25 % der Referenz;
   `blur` = normierte Schärfe < 0,45 × Referenz; `moved` = Verschiebung > 2 % bei Antwort > 0,04 oder Antwort
   < 0,5 × gleitende Basis; `frozen` = mittlere Differenz zum Vorframe < 0,05 (nachts gemessenes Minimum 0,45).
3. `health_alert_after_s` = 60 s (Default) passt; zwei Referenzen Tag/IR automatisch anlegen.

## 5. B1 – Zeitmaschine: SigLIP 2 vs. MobileCLIP2 (`histtest`)

**Aufbau.** `open_clip_torch==3.2.0` (im Test-Image, nicht im Worker), Bild-Turm fp16 auf der GPU, Text-Turm auf
der CPU. 320 Bilder aus den Testvideos (Büro/Wohnung mit liegender/stehender Person, Gesten, Supermarktregal,
Flaschen, Flur, Straße), Wahrheit aus Frame-Labels bzw. YOLO11s. 9 auswertbare Anfragen, jeweils deutsch und
englisch (z. B. „eine Person liegt auf dem Boden“, „ein Supermarktregal mit Waren“, „ein leerer Raum ohne Menschen“).

| Modell | Hit@5 de/en | P@5 de/en | mAP de/en | Bild ms (B1/B8) | Text ms (CPU) | VRAM Bild-Turm (torch reserviert) |
|---|---|---|---|---|---|---|
| **SigLIP 2 ViT-B/16-256** (`webli`) | 1,00 / 1,00 | 0,93 / 0,98 | **0,80** / 0,93 | 13,5 / 49 | 37 | **290 MiB** |
| MobileCLIP2-S0 (`dfndr2b`) | 0,89 / 0,89 | 0,80 / 0,87 | 0,72 / 0,79 | 19,6 / 43 | 21 | 136 MiB |

- MobileCLIP2 verfehlt „Auto auf der Straße“ komplett und ist bei „Arme verschränkt“ deutlich schwächer.
- Deutsch kostet bei SigLIP 2 ≈ 0,13 mAP gegenüber Englisch (v. a. „Menschen gehen durch einen Gang“ 0,48 vs. 0,98,
  „leerer Raum“ 0,45 vs. 0,68). Kurze, gegenständliche deutsche Anfragen funktionieren gut.
- Der Bildsatz ist einfacher als ein echtes Zuhause (Quellen klar getrennt); die Werte sind Obergrenzen.
- **Dedupe `cam_3`** (SigLIP 2 auf der CPU, alle 30 s, 00:31–02:01, Nacht/IR, 180 Frames): Abstand aufeinander
  folgender Embeddings Median 0,0013 (p90 0,0023); bei jeder Schwelle 0,02–0,12 wurde nur **1 von 180** Frames
  gespeichert (Dedupe-Quote 99,4 %). Die 80 % aus dem Entwurf sind nachts weit übertroffen; tagsüber mit
  Bewegung nicht gemessen. Empfehlung `history_min_change` = 0,05 (Rauschen liegt 20× darunter).

**Go/No-Go:** **Go** mit SigLIP 2 (Plan „+0,25–0,4 GB“ ✓, Abnahme „VRAM steigt um ≤ 0,5 GB“ ✓).

**Empfehlungen B1**
1. `open_clip_torch==3.2.0`, Modell `ViT-B-16-SigLIP2-256` / `webli`; nur der Bild-Turm in den VRAM (fp16), Text auf
   der CPU (37 ms je Anfrage reichen für die Suche). Zusatzpaket im Worker: `open_clip_torch` mit `--no-deps`,
   dazu `transformers`/`sentencepiece` für den Gemma-Tokenizer – oder den Text-Turm im Backend rechnen.
2. Anfragen im Backend zusätzlich ins Englische erweitern (Synonymliste/LLM optional) – bringt laut Messung +0,13 mAP.
3. MobileCLIP2 nur, falls das VRAM-Budget nach YOLOE-Lasttest nicht reicht.

## 6. A2 – Frag die Kamera: VLM (`vlm/bench.py`)

**Aufbau.** Eigener Probe-Container `vlm/Dockerfile.bench` (pytorch 2.5.1-cuda12.4, transformers 4.57.6,
torchao 0.11.0, bitsandbytes 0.45.5), Gewichte im Test-Volume `vg-w0_models`. Fragensatz aus
`tests_feasibility/vlmset.py`: 22 Ja/Nein-Fragen zu 21 Bildern (liegende/stehende Person, Arme verschränkt, Hand
gehoben, Regal, Flaschen, Person/Auto ja/nein, Licht an/aus, rotes/blaues Rechteck), von Hand geprüft. Gemessen
**neben dem laufenden Worker inklusive YOLOE** (Grundlast vor dem Test 2,90 GB).

| Modell (Revision gepinnt) | Laden | 1. Antwort | Antwort (Median) | 2880×1616-Bild | richtig | torch reserviert | Gerät gesamt Spitze | Deutsch |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **Moondream 2 INT4** (2025-04-14, Apache-2.0) | 2,1 s | 2,1–3,5 s | **0,99 s** | 1,5 s | **21/22** | 2,75 GB | **5,70 GB** | nein (wiederholt die Frage) |
| Qwen2.5-VL-3B NF4 (bitsandbytes, Qwen-Research-Lizenz) | 10 s | 1,0–1,4 s | 0,80 s | 1,7 s | 20/22 | 2,63 GB | 5,59 GB | **ja** („Ja, die Person liegt auf dem Boden.“) |
| SmolVLM-500M (bf16, Apache-2.0) | 2,3 s | 0,6–0,8 s | 0,50 s | 0,8 s | 20/22 | 1,53 GB | 4,49 GB | knapp („Ja.“) |
| SmolVLM-256M (bf16) | 1,8 s | 0,7 s | 0,51 s | 1,0 s | 19/22 | 1,02 GB | 4,03 GB | nicht geprüft |

- **torchao 0.11.0 + torch 2.5.1 funktioniert** (offener Punkt aus `00-wellenplan.md` §6 erledigt); `trust_remote_code`
  mit gepinnter Revision; `model.compile()` nicht getestet.
- Fehler: Moondream hält das fast schwarze Bild für „Licht an“; Qwen übersieht einmal die liegende Person und die
  verschränkten Arme; SmolVLM-500M verneint das Supermarktregal und sieht eine sitzende Person als liegend.
- Während der VLM-Fragen blieb die Detektion bei 5,0 fps; die YOLOE-Durchläufe dauerten statt 41 ms 100–300 ms
  (GPU-Konkurrenz), es fielen keine Takte aus.
- **Budget:** Mit YOLOE geladen liegt Moondream bei 5,70 GB (zweiter Lauf; erster Lauf bei höherer Grundlast
  5,78 GB) – Grenze 5,6 GB ✗. YOLOE-11m belegt laut Phase 3 ≈ 0,33 GB (torch) [G]; nach dem Entladen per Lease
  ≈ **5,4 GB** [S aus G] ✓. Qwen NF4 lag mit YOLOE knapp unter
  der Grenze (5,59 GB), braucht aber 10 s zum Laden.

**Go/No-Go:** **Go – nur mit GPU-Lease** (YOLOE entladen + `empty_cache` vor dem Laden), Antwortzeit < 5 s ✓,
Trefferquote ≥ 85 % ✓ (95 %), Detektion ≥ 4,5 fps ✓.

**Empfehlungen A2**
1. Standard **Moondream 2 INT4** (`moondream/moondream-2b-2025-04-14-4bit`, Rev. `a89c592…`), Prompts intern
   Englisch, Bild unverändert übergeben (Moondream kachelt selbst). Lease-Bedarf `need_mb` ≈ 3000 (2,75 GB torch + eigener CUDA-Kontext).
2. Ohne Lease (z. B. Inventar darf nicht pausieren): **SmolVLM-500M** als Rückfall (Spitze 4,5 GB, 0,5 s).
3. Deutsch: Qwen2.5-VL-3B NF4 ist die einzige Variante mit brauchbarem Deutsch; Lizenz nur nicht-kommerziell (wie
   `buffalo_l`), 10 s Ladezeit → nur als Option `vlm_model`.
4. `vlm_idle_unload_s` ≈ 120 s; die erste Antwort nach dem Laden dauert 2–3,5 s, danach ≈ 1 s.
5. Freien VRAM für die Lease per **NVML** prüfen, nicht per `cudaMemGetInfo` (WSL2).

## 7. A1 – Flackern der Inventar-Zustände auf `cam_3` (`flickertest`)

**Aufbau.** Nur Mitlesen von `vg/internal/inventory/3` (voller Zustand je Durchlauf, Intervall 3 s, YOLOE-11m,
`confirm_n` 2, `miss_m` 3). Ausgewertet: Zustandswechsel present↔missing je Gegenstand, nie bestätigte
Kandidaten, `changes` des Workers. Läufe:

| Lauf | Zeit | Durchläufe | Szene | appeared / disappeared pro Stunde | Gegenstände (IDs) | nie bestätigt |
|---|---|---:|---|---:|---:|---:|
| 1 | 23:25, 10 min | 200 | abends, Licht an, Szene wurde verändert | 108 / 138 | 40 | 20 |
| 2 | 23:36, 20 min | 392 | Übergang, Person im Raum | 279 / 261 | 73 | 34 |
| 3 | 23:57, 49 min | 978 | **Nacht, IR, ruhige Szene** | **301 / 304** | 150 | 98 |
| 4 | 00:50, 45 min | 900 | Nacht, IR, ruhige Szene | 249 / 249 | 144 | 106 |

(Lauf 3 wurde nach 49 min durch eine doppelte MQTT-Client-ID beim Start von Lauf 4 getrennt – behoben; gewertet
sind die 978 Durchläufe bis dahin.)

Auffälligste Labels in der ruhigen Nacht (Lauf 3): `mug` 182 Wechsel bei Score 0,41 (knapp über
`min_score` 0,35), `chair` 118 Wechsel über 40 IDs, `pillow` 82 Wechsel über 44 IDs, `remote` 33, `phone` 28 (Scores
0,38–0,40). Stabil waren `laptop` (0,95), `bottle` (0,89), `sofa` (0,75): keine bzw. eine Änderung.

**Andere Hysterese nachgespielt** (Lauf 4, Treffer-Folgen je Gegenstand; „verschwunden“-Ereignisse bestätigter
Gegenstände pro Stunde, Intervall 3 s):

| `confirm_n` \ `miss_m` | 3 (heute, 9 s) | 5 (15 s) | 10 (30 s) |
|---|---:|---:|---:|
| 2 (heute) | 163 | 92 | 40 |
| 3 | 79 | 41 | **12** |

Werte für `miss_m` 10 sind Untergrenzen, weil der Worker `missing`-Einträge nach 10 Durchläufen fallen lässt und
die Folgen dort enden. Lauf 4 nach Labels: `mug` 165 Wechsel (10 IDs, Score 0,41), `chair` 126 (35 IDs),
`pillow` 35 (39 IDs), `phone` 21 (36 IDs, Score 0,37); 9 Gegenstände blieben die ganzen 45 min stabil `present`.

**Go/No-Go:** **No-Go** für Hinweise auf Basis einzelner Zustandswechsel; **Go** mit Filter (siehe unten). Ein
Wegnehm-Test mit Schlüsselbund/Tablettendose war nachts nicht möglich (niemand vor Ort) → offen.

**Empfehlungen A1/B2**
1. **Defaults ändern:** `inventory_confirm_n` 2 → **3**, `inventory_miss_m` 3 → **10** (≈ 30 s bei 3 s Intervall);
   laut Simulation −93 % „verschwunden“-Ereignisse. Den Abwurf von `missing`-Einträgen im Worker (heute nach
   10 Durchläufen) an `miss_m` koppeln.
2. **Score-Hysterese im Worker:** `present` erst ab `min_score + 0,05`, `missing` erst, wenn der Score in
   `miss_m` Läufen unter `min_score − 0,05` bleibt oder gar nicht erscheint. Die meisten Wechsel stammen von
   Gegenständen, deren Score um die Schwelle pendelt.
3. **`condition_delay_s` ≥ 120 s** und B2-Rauschfilter **≥ 120 s** (wie im Entwurf) – ein Gegenstand gilt erst als
   verschwunden, wenn er länger als 2 min fehlt.
4. IDs zusammenführen: Neue Kandidaten mit gleichem Label und IoU ≥ 0,3 zu einem kürzlich `missing` gewordenen
   Gegenstand sind derselbe Gegenstand (verhindert 40 Stuhl-IDs pro Stunde).
5. Nachts (IR) Labels mit Farbbezug bzw. Score < 0,45 nicht für Hinweise verwenden; `inventory_untouched` nur auf
   Gegenstände mit stabilem Score (≥ 0,6) anbieten.
6. Wegnehm-Test (Schlüsselbund, Tablettendose) tagsüber nachholen; erst danach A1-Leitbeispiel abnehmen.

## 8. Testvideos (`testcam/`)

`testcam/download-extra-videos.sh` lädt die Daten (nicht im Repo) und baut drei zusätzliche Kameras; `mediamtx.yml`
startet sie nur, wenn die Datei existiert (geprüft mit einem Wegwerf-mediamtx auf Port 18554):

| Pfad | Datei | Inhalt | Lizenz |
|---|---|---|---|
| `rtsp://testcam:8554/cam3` | `sturz.mp4` | 10 UR-Fall-Stürze mit je 10 s Liegen (Standbild) + 10 Alltagsszenen, 640×480 | CC BY-NC-SA 4.0 |
| `rtsp://testcam:8554/cam4` | `gesten.mp4` | UTD-MHAD: Winken, Arme verschränken, Wischen links/rechts, Klatschen, Hinsetzen/Aufstehen, Gehen (4 Personen) | frei für Forschung, Zitat erbeten |
| `rtsp://testcam:8554/cam5` | `regal.mp4` | Intel `store-aisle-detection` + `bottle-detection` | CC BY 4.0 |

Einzelclips und Label-CSVs liegen unter `testcam/videos/raw/` (für `posetest`). Der UTD-Server liefert nur
≈ 40 KB/s; `testcam/remote-unzip.py` lädt deshalb nur die 96 benötigten Clips per HTTP-Range aus dem 1,1-GB-ZIP.
**Nicht gefunden:** frei verfügbare Videos mit *gehaltenen* Armgesten in Raumkamera-Perspektive und mit einem
Sturz in einer echten Wohnung (UR Fall ist Büro/Wohnung, 320×240) → **eigene Aufnahmen bleiben nötig** (C1/D1-Abnahme).
Für Regal-Füllstände (C4) gibt es nur das Intel-Video, keine voll/halb/leer-Serie → `supplytest` braucht eigene Fotos.

## 9. Offen (nicht gemessen)

- **Lasttest 16 Kameras mit allen Modellen gleichzeitig** (`selftest --replicate 8` + Pose + SigLIP): gehört laut
  Wellenplan zum Wellenabschluss; hier nur Einzelmessungen neben dem Haupt-Worker (1 Kamera).
- **C2 ReID (OSNet)** aus dem `posetest`-Entwurf: ONNX-Export über `torchreid` nicht versucht.
- Wegnehm-Test kleiner Gegenstände (A1), Haltegesten (D1), eigene Handyfotos (B4), Tag/Nacht-Wechsel (C5),
  Dedupe tagsüber (B1) – brauchen jemanden vor Ort bzw. Tageslicht.
- Moondream mit `model.compile()` (schnellere Antworten) und VLM-Fragen an echte `cam_3`-Bilder (bewusst
  ausgelassen: Personen im Bild, nur öffentliche Testbilder verwendet).

## 10. Reproduzieren

```bash
# einmalig
docker build -t vg-w0-feasibility:test -f worker/vg_worker/tests_feasibility/Dockerfile worker/vg_worker/tests_feasibility
docker build -t vg-w0-vlm:bench -f vlm/Dockerfile.bench vlm
testcam/download-extra-videos.sh
# Tests (GPU-Tests nehmen ~/vg-gpu.lock; Ausgabe nach $VG_W0_OUT, Daten nach $VG_W0_DATA)
T=worker/vg_worker/tests_feasibility/run.sh
$T posetest --parts load,lying,gestures --rtsp rtsp://testcam:8554/cam1
$T itemtest --fetch 60
VG_W0_GPU=0 $T healthtest synth --src rtsp://testcam:8554/cam1,rtsp://testcam:8554/cam2,cam_3 --frames 30 --every 3
VG_W0_GPU=0 $T healthtest observe --src cam_3 --hours 5 --every 10
$T histtest bench
VG_W0_GPU=0 $T histtest dedupe --models siglip2 --src cam_3 --minutes 90 --every 30 --device cpu
$T vlmset && vlm/run-bench.sh "$VG_W0_DATA/vlmset" "$VG_W0_OUT" --models moondream-int4,smolvlm-500m,qwen25vl-3b-nf4
VG_W0_GPU=0 $T flickertest --camera 3 --minutes 45
# Aufräumen
docker volume rm vg-w0_models   # ≈ 12 GB Testgewichte
```

Die Roh-Ergebnisse (JSON) liegen nicht im Repo; Bilder von `cam_3` wurden nie gespeichert.

## 11. C4-VLM-Modus – Vorräte per Bildmodell (Welle 4, `scripts/vlm_supply_bench.py`)

Stand: 25.10.2026, früher Morgen (Zweig `w4/c4-vlm-modus`). Gemessen über die A2-Schnittstelle (`POST /infer` des
laufenden Dienstes `vlm` im Haupt-Stack, Moondream 2 INT4, GPU-Lease durch den Dienst, Client unter
`flock ~/vg-gpu.lock`), Modus `query`, Bilder unverändert. Keine Bilder von `cam_3`; nur das öffentliche
Intel-Video `regal.mp4` (CC BY 4.0, Abschnitt `bottle-detection`) und synthetische Bilder.

**Datensatz (36 Bilder, Wahrheit von Hand gezählt):**
- 16 echte Frames (640×360) mit 1–3 Wasserflaschen auf einem Tisch, teils mit Hand im Bild;
- 3 Ausschnitte (0 bzw. 1 Flasche);
- 5 nebeneinander gesetzte Frames (4, 5, 6, 8, 9 Flaschen; 1280–1920 px breit) als Ersatz für ein volleres Regal;
- 12 synthetische Gläser (Kaffeebohnen bzw. orangefarbener Inhalt) mit Füllstand 100/75/50/25/8/0 %.

| Messart / Frage | Ergebnis | Zeit (Median, warm) |
|---|---|---|
| Anzahl, Vorlage „How many bottles are there? Answer with a number only.“ | **0–3 Flaschen: 19/19 exakt** (Antwort nur die Ziffer); 4→3, 5→4, 6→4, 8→4, 9→5 (zu niedrig) | 0,74 s |
| Anzahl, „How many bottles are visible?“ | gleiche Zählung (Antwortsatz „There are three bottles …“, Parser liest Zahlwörter); 8→6, 9→6 | 0,91 s |
| Zustand ok/knapp/leer bei `min_count` 2 | **24/24** richtig (Unterzählung großer Mengen bleibt ≥ 3 → „ok“) | – |
| Füllstand, Vorlage „How full is the jar? Answer with one word: full, half, low or empty.“ | voll 2/2 „full“, leer 2/2 „empty“; 75 %/50 %/25 % fast immer „full“ (einmal „half“); 8 % einmal „empty“, einmal „full“ | 0,75 s |
| Füllstand als Prozent („What percentage … is filled?“) | 50 % bei echten 8 %, 100 % bei 25 % – unbrauchbar | 0,97 s |
| Ja/Nein „Is the jar empty or almost empty?“ / „less than half full?“ | **immer „Yes“** (auch beim vollen Glas) – unbrauchbar | 0,9 s |

Erste Frage nach dem Laden 6,0 s (Lease + Laden), danach 0,73–0,76 s je Frage. Zwei Läufe lieferten identische
Antworten (deterministisch).

**Go/No-Go:**
- **Anzahl: Go mit Grenze** – das Modell zählt kleine Mengen (0–3) zuverlässig und große zu niedrig. Daher
  `min_count` ≤ 3 (dann sind alle Zustände richtig, auch bei voller Ablage). Für Regale mit vielen Artikeln bleibt
  `inventory_count` (YOLOE) die Messart; das Bildmodell bestätigt dort nur Wechsel (`verify_vlm`, `min_count` ≤ 3).
- **Füllstand in Stufen: No-Go** – Moondream trennt „halb“/„wenig“ nicht von „voll“, Ja/Nein-Fragen sind
  zustimmungsverzerrt. **Eingeschränkte Variante:** Messart `fill` meldet nur `ok`/`out` („leer erkannt“, im Test
  11/12 richtig bei Grenze 15 %), nie `low`. Gestufte Füllstände weiter über die Art `fill` (DINOv2-Referenzbilder).
- Grenzen der Messung: nur ein Gegenstandstyp (Flaschen) und synthetische Gläser; kein echter Vorratsschrank, keine
  Nacht/IR-Bilder, keine Verdeckung durch hintere Reihen. Vor einer Abnahme mit eigenen Fotos nachmessen
  (`truth.json` mit `measure`/`item`/`truth`, siehe Kopf von `scripts/vlm_supply_bench.py`).
