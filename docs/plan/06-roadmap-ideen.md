# Roadmap – Ideen nach der aktuellen Umsetzung

Stand: 2026-10-24. Baut auf Phasen 1–6 auf (Gesichter, API-Keys, Gegenstände, MCP; siehe `00-gesamtplan.md`).
Bewusst **nicht** aufgenommen: Grundriss-Ansicht, Chat-Seite „Frag dein Haus“, Privatsphäre-Modus.

Leitbild: VisionGuard soll den Haushalt nicht nur beobachten, sondern verstehen – wissen, wo Dinge sind, was sich
verändert und was gleich passiert. Weiterhin gilt: **keine Daueraufzeichnung von Video**.

## Priorität A – nächste Schritte

### A1. Proaktiver Hinweis („Du vergisst deinen Schlüssel“)
- Regel-Engine um Bedingungen auf das Gegenstandsgedächtnis erweitern: „Gegenstand X ist / ist nicht vorhanden
  (in Kamera/Raum)“, verknüpft mit bestehenden Auslösern (Person betritt Zone „Haustür“, Identität, Zeitplan).
- Aktionen: Push (neu, z. B. ntfy/Pushover/Webhook), Durchsage über Home Assistant (später), MQTT.
- Beispiele: Schlüssel/Handy/Geldbörse beim Verlassen; Regenschirm + Regenvorhersage; Medikamente morgens unberührt.
- Abhängigkeit: Phase 5 (Gegenstandsgedächtnis). Aufwand: M.

### A2. „Frag deine Kamera“ – Bildverständnis-Modell (VLM)
- Lokales Vision-Language-Modell (Kandidaten: Moondream 2, Qwen2.5-VL-3B, quantisiert) beantwortet freie Fragen zum
  aktuellen Bild: „Ist der Herd aus?“, „Steht das Fenster offen?“.
- MCP-Werkzeug `ask_camera(camera, question)` und REST-Endpunkt; nur auf Anfrage, lazy geladen.
- Zuerst Machbarkeitstest: VRAM (6 GB, davon ~2,7 GB belegt), Antwortzeit, Qualität. Optionaler Fallback über Claude
  (dann nicht mehr rein lokal – nur mit ausdrücklicher Freigabe je Kamera).
- Grundlage für A3, B1 und B3. Aufwand: M (Test) + M (Integration).

### A3. Virtuelle Sensoren
- Bereich im Bild markieren + Frage/Zustand („Fenster offen?“, „Licht an?“, „Waschmaschine blinkt?“).
- Auswertung periodisch (VLM aus A2 oder schlanker Klassifikator/Helligkeit je nach Sensor), Ergebnis als Zustand
  mit Hysterese; Ausgabe über REST, MCP, MQTT (später Home-Assistant-Discovery), nutzbar in Regeln.
- Aufwand: M–L.

## Priorität B – Gedächtnis und Verständnis

### B1. Haus-Zeitmaschine (semantische Suche in der Vergangenheit)
- Alle N Sekunden je Kamera nur ein CLIP-Embedding + winziges Vorschaubild speichern (kein Video).
- Textsuche: „Wann stand das rote Paket im Flur?“, „Wann lag die Brille auf dem Sofa?“ → Zeitpunkte mit Vorschau.
- Speicherbedarf abschätzen (Embedding ~2 KB), eigene Aufbewahrungsfrist, Vektorsuche (pgvector).
- MCP-Werkzeug `search_history(query, since?, camera?)`. Aufwand: L.

### B2. „Was hat sich verändert?“
- Vergleich des Gegenstandsgedächtnisses zwischen zwei Zeitpunkten (z. B. seit heute Morgen / seit ich weg bin):
  neu aufgetaucht, verschwunden, verschoben. Erkennt Paketzustellung, fehlende Dinge, Unordnung.
- REST + MCP `what_changed(since)`. Abhängigkeit: Phase 5 (inventory_history). Aufwand: S–M.

### B3. Tagesbericht
- Abends (oder auf Anfrage) eine Zusammenfassung aus Events, Identitäten, Gegenständen, virtuellen Sensoren:
  „Postbote 10:42, 2 Pakete. Anna kam 17:10. Hund 4× im Garten. Bad-Fenster 3 h offen.“
- Textgenerierung per LLM (lokal oder Claude) aus strukturierten Daten; Versand per Push/Mail/MQTT. Aufwand: M.

### B4. Eigene Gegenstände wiedererkennen („mein Schlüssel“)
- Gegenstand per Handyfoto anlernen; DINOv2-Embeddings (wie Haustiere) unterscheiden eigene von fremden Exemplaren.
- Erweitert Gegenstandsgedächtnis um benannte Exemplare. Aufwand: M.

## Priorität C – Sicherheit, Gesundheit, Alltag

### C1. Sturz- und Notfallerkennung
- YOLO11-pose; Regel „Person liegt seit > N Minuten am Boden / regungslos“ → Alarm mit Eskalation.
- Fehlalarme (Sofa, Yoga) über Zonen und Dauer begrenzen. Aufwand: M–L.

### C2. Person über mehrere Kameras verfolgen
- Wiedererkennung ohne Gesicht (Kleidung/Statur, Person-ReID-Embeddings), kurzzeitig gültig (z. B. 1 h).
- Beantwortet „Wer ist gerade wo im Haus?“; MCP `who_is_where` wird genauer. Aufwand: L.

### C3. Haustier-Tagebuch
- Zonen „Napf“, „Wasser“, „Katzenklo“: Besuche je Tier zählen, Abweichungen vom Normalverhalten melden.
- Aufwand: S–M (baut auf Tier-Identifikation und Zonen auf).

### C4. Vorräte
- Kamera auf Vorratsregal/Kühlschrank; Füllstand bzw. Vorhandensein ausgewählter Artikel → Einkaufsliste
  (z. B. Home-Assistant-Einkaufsliste, später). Aufwand: M–L (abhängig von A2).

### C5. Kamera-Gesundheit
- Erkennen: Kamera verschoben (Bildvergleich mit Referenz), verdeckt, unscharf, zu dunkel, Standbild.
- Warnung im Systemstatus und per Push, statt stiller Fehlerkennung. Aufwand: S–M.

## Priorität D – Bedienung, Lernen, Skalierung

### D1. Gesten-Steuerung
- Handzeichen in eine Kamera lösen Aktionen aus („Licht aus“, „Alarm stumm“, „Szene Kino“).
- Grundlage: YOLO11-pose aus C1 (Körperhaltung) plus Hand-Keypoints bzw. Gesten-Klassifikator; Geste muss
  ≥ 1 s stabil sein, optional nur von bekannten Identitäten und nur in definierten Zonen (gegen Fehlauslösung).
- Gesten als neuer Regel-Auslöser; Aktionen wie bisher (MQTT, Webhook, später Home Assistant). Aufwand: M.

### D2. Lernen durch Rückfrage
- Unsichere Erkennungen (Score knapp unter/über der Schwelle, neue Gegenstände, mögliche Identitäts-Treffer) landen in
  einer Warteschlange „Bitte bestätigen“: Ja/Nein in der Oberfläche oder per Push mit Bildausschnitt.
- Antworten passen Schwellen je Gegenstand und Kamera an, sammeln Positiv-/Negativbeispiele (später Few-shot bzw.
  Feinabstimmung) und speisen Identitäten (Vorschläge aus Phase 1).
- Begrenzt: max. N Rückfragen pro Tag, nichts doppelt fragen. Aufwand: M.

### D3. Weitere Rechner für die KI (verteilte Worker)
- Mehrere Worker-Instanzen (zweiter PC, NVIDIA Jetson) teilen sich die Kameras; das Backend verteilt Kameras nach
  freier Kapazität (GPU-Speicher, fps) und verschiebt sie bei Ausfall eines Knotens.
- Voraussetzungen: Worker-ID in allen internen MQTT-Nachrichten (`vg/internal/status`, `system` je Knoten),
  Zuweisung `camera → worker` im Backend, Medienablage für entfernte Knoten (Upload per API statt gemeinsames Volume),
  go2rtc-Zugriff über das Netz, Auth für entfernte Worker.
- Erst einplanen, wenn mehr Kameras kommen; die Schnittstellen aber schon jetzt nicht verbauen. Aufwand: L.
