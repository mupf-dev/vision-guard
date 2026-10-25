# Plan: Identitäten per Mehrfachauswahl bearbeiten und Personen-Erkennung verbessern

Stand: 2026-10-25. Grundlage: drei Analysen (Oberfläche/API, Erkennungs-Pipeline, Daten der laufenden Installation).

## 1. Ausgangslage

### Mehrfachauswahl
- Zusammenführen und Löschen gibt es nur einzeln im Detaildialog (`frontend/src/pages/Identities.tsx`:
  `MergeDialog`, `IdentityDetail`). Keine Checkboxen, keine Sammelaktionen – weder in Oberfläche, API, Mock noch MCP.
- Backend: `POST /identities/{id}/merge` und `DELETE /identities/{id}` arbeiten je Identität, committen einzeln, senden
  je eigene WS-Nachrichten (jede löst im Frontend einen teuren Neuladevorgang mit Vorschlägen aus).

### Daten (≈ 12 h, nur Gesichter)
| Kennzahl | Wert |
|---|---|
| Identitäten | 33, davon 2 benannt |
| mit nur 1–2 Bildern | **61 %**, Lebensdauer im Median **0 h** |
| Gesichtsbilder | Breite Median **100 px**, Qualität Median 0,44 |
| Personen-Ereignisse mit Identität | 77 % |
| Benannte untereinander | max. Ähnlichkeit 0,17 → **keine Verwechslung** |
| Fragmente an Benannten | je Benannter 2–13 unbenannte Fragmente (Ähnlichkeit bis 0,72) |
| aktuelle Schwelle | 0,30 (passt laut Daten; höchstens 0,35) |

**Befund:** Das Problem ist **Zersplitterung**, nicht Verwechslung. Dieselbe Person erzeugt immer neue „Person #N“.

### Ursachen (Pipeline)
1. Gesichter werden aus dem herunterskalierten Analysebild geschnitten (2880 → 1920 px), Gesichter von 18–36 px
   werden auf 240 px hochgerechnet → unsichere Merkmale.
2. Jeder Fehltreffer legt sofort eine neue Identität an – ohne Mindestqualität.
3. Pro Track nur 3 Versuche in den ersten ≈ 1,2 s; kein bestes Bild bzw. Mittelwert über den ganzen Track.
4. Qualitätsmaß ohne Kopfdrehung und Unschärfe → Profilbilder landen als „gute“ Beispiele in der Galerie.
5. Galerie = Top-20 nach Qualität, ohne Vielfalt (Tag/Nacht/Winkel); ein schlechtes Beispiel zieht Fremde an.
6. Vorschläge vergleichen nur Unbenannt → Benannt, unbenannte Fragmente werden nie untereinander gebündelt.

> „Training“ heißt hier **nicht** ein neuronales Netz nachtrainieren (ArcFace bleibt; dafür sind die Datenmengen viel
> zu klein und das Risiko hoch), sondern: bessere Gesichtsbilder sammeln, Galerie kuratieren, Fragmente nachts
> zusammenführen, Schwellen je Person lernen. Das ist der wirksame Hebel.

## 2. Arbeitspakete

### A – Mehrfachauswahl: zusammenführen und löschen (auch Unbekannte)
- **API** (CONTRACT Abschnitt D ergänzen):
  - `POST /identities/bulk-delete {ids: int[1..500]}` → `{deleted: int[]}`; Ereignisse bleiben (ohne Identität),
    Bilder werden nach dem Commit gelöscht, Tier-/Gegenstands-Hooks je Art.
  - `POST /identities/bulk-merge {ids: int[1..500], into_id: int}` → `{target: Identity, merged: int[]}`;
    immer **vollständiges** Zusammenführen (alle Bilder und Ereignisse), alles in **einer** Transaktion unter einem
    Lock; Dateien erst nach dem Commit verschieben; gemischte Arten → 422; Bildlimit kumulativ prüfen (manuelle
    Bilder), danach Galerie kürzen.
  - WS gebündelt: `{"type":"identities","action":"merged|deleted","ids":[…],"into_id":…}` + Ziel-Identity;
    Frontend lädt einmal (entprellt) neu.
- **Oberfläche** (`Identities.tsx`, Unbekannt und Bekannt):
  - Schalter „Auswählen“; Häkchen auf jeder Karte, Umschalt-Klick für Bereiche, „Alle (gefilterten) auswählen“,
    Zähler.
  - Aktionsleiste: **Zusammenführen …** (Ziel: eine der Ausgewählten oder eine andere bekannte Person; Vorschlag:
    benannte > meiste Ereignisse > meiste Bilder; Vorschau „7 Identitäten, 43 Bilder, 18 Ereignisse → Anna“) und
    **Löschen …** (Bestätigung mit Anzahl, Hinweis „Ereignisse bleiben ohne Person“).
  - Hilfe: „Ähnliche auswählen“ – markiert alle Unbekannten, die einer gewählten Person ähneln (aus den
    Vorschlägen bzw. dem Nachtlauf).
- Typen/API/Mock; optional MCP-Tool später (nicht vorgesehen).

### B – Bessere Gesichtsbilder (Worker) – größter Hebel
- **Hochauflösende Ausschnitte:** Gesichter aus dem Bild in voller Auflösung schneiden (bei `cam_3` 2880×1616 statt
  1920). Erst Machbarkeitstest: CPU/RAM beim Dekodieren in voller Auflösung bzw. nur für Kameras mit
  Gesichtserkennung; Alternative: Ausschnitt aus dem Hauptstrom nur auf Anforderung.
- **Qualitätsmaß:** Kopfdrehung (Gier/Nicken aus den 5 Gesichtspunkten), Unschärfe (Laplace-Varianz),
  Gesichtsgröße; Profile und unscharfe Bilder werden nicht als Beispiel gespeichert.
- **Pro Track sammeln statt 3× sofort:** über die ganze Track-Dauer die besten K Gesichter sammeln (gedrosselt);
  früh eine vorläufige Zuordnung, am Track-Ende eine endgültige mit gemitteltem Merkmal aus den besten Bildern.

### C – Zuordnungslogik (Backend)
- **Qualitätsgrenze für neue Identitäten:** Neu anlegen nur ab guter Qualität (Start: ≥ 0,35 und Gesicht ≥ 80 px);
  schwächere Bilder dürfen nur bestehenden Personen zugeordnet werden, sonst bleibt der Track ohne Identität.
- **Zuordnung pro Track:** endgültige Identität aus dem Track-Mittel (siehe B), nicht „letzte Antwort gewinnt“.
- **Abgleich:** Mittel der besten 3 Treffer bzw. Zentroid statt Einzelmaximum; Abstand zum Zweitbesten
  berücksichtigen (sonst Rückfrage statt Zuordnung).
- **Mehr und vielfältigere Beispiele:** benannte Personen bis 40 Bilder (statt 20), Auswahl nach Vielfalt (k-Center)
  statt nur Qualität, Tag/Nacht getrennt halten.

### D – Täglicher Trainingslauf („Nachtlauf“, Standard 03:00, Einstellung + „Jetzt ausführen“)
1. **Fragmente bündeln:** alle unbenannten Gesichts-Identitäten untereinander und gegen Benannte clustern.
   - sicher (Start ≥ 0,55 und deutlicher Abstand zum Zweitbesten): automatisch zusammenführen –
     **nur Unbenannt → Unbenannt/Benannt**, nie zwei Benannte;
   - unsicher (0,40–0,55): Rückfrage „Bitte bestätigen“ (`identity_match`, gibt es schon; „Nein“ wird gemerkt),
     höchstens N pro Tag.
2. **Galerie kuratieren:** Ausreißer je Person (weit vom Zentroid) als Negativbeispiel markieren, Vielfalt
   herstellen, manuelle Bilder bleiben.
3. **Schwelle je Person** aus der Streuung ihrer Bilder (neue Spalte, Grenzen 0,25–0,45).
4. **Aufräumen:** Einzelbild-Fragmente ohne Ereignisse nach X Tagen entfernen (gibt es teilweise).
5. **Bericht:** Hinweis „Nachtlauf: 12 Fragmente zusammengeführt, 4 Rückfragen, Galerie von Anna aktualisiert“;
   Probelauf-Vorschau in der Oberfläche; jede automatische Zusammenführung bleibt im Verlauf nachvollziehbar und
   lässt sich per Teil-Merge zurücksetzen.

### E – Messen statt raten
- Auswerteskript (wie die Datenanalyse): Fragmente je benannter Person, Einzelbild-Anteil, Anteil Personen-Ereignisse
  mit Identität, Fehlzuordnungen (Rückfragen mit „Nein“). Messung vorher → nach B/C → nach einer Woche D.
- Zielwerte: Einzelbild-Identitäten < 20 %, Fragmente je bekannter Person ≤ 1 pro Tag, keine Verwechslung von
  Benannten.

## 3. Umsetzung mit Agenten

| Schritt | Agent(en) | Inhalt |
|---|---|---|
| 0 Kickoff | 1 | CONTRACT (Bulk-API, WS, neue Spalten/Settings, Nachtlauf-Settings), Gerüste, Messung „vorher“ |
| 1 parallel | 3 in eigenen Worktrees | **A** Mehrfachauswahl (Backend + UI) · **B** Worker-Crops inkl. Machbarkeitstest volle Auflösung (GPU/CPU unter `flock`) · **C** Zuordnungslogik |
| 2 | 1 | **D** Nachtlauf (baut auf C auf), Probelauf an echten Daten (nur Vorschau, kein Auto-Merge ohne Freigabe) |
| 3 Integration | 1 | Merge, Build, e2e (Testkamera mit Gesichtern), Lasttest 16 Kameras, Messung „nachher“, Doku |
| 4 Prüfung | 2 | Robustheit/Datenverlust (Merge-Atomarität, Rücksetzbarkeit) und Bedienung |

Aufwand grob: A 1–1,5 Tage, B 1,5–2, C 1, D 1,5, Integration/Messung 1 → ≈ 6–7 Personentage, mit Agenten in
einem Lauf von einigen Stunden.

## 4. Offene Entscheidungen
1. Darf der Nachtlauf **sichere Fälle automatisch zusammenführen** (nur unbenannte Fragmente), oder nur Rückfragen
   stellen?
2. Gesichter aus **voller Auflösung** schneiden kostet mehr CPU beim Dekodieren – akzeptabel, wenn der
   Machbarkeitstest im Rahmen bleibt?
3. Sollen bestehende Fragmente beim ersten Nachtlauf gleich mit aufgeräumt werden (vorher Probelauf zum Ansehen)?
