# Roadmap

Stand: 0.10.0. Erledigt sind Phase 1 (Analyse, Editor, Export, Stapel),
Phase 2 Teil 1 (Gerätesteuerung über Intiface, Simulation), Phase 3
(Player mit Playlist, Tracking mit Abschnitten und Tiefen-Kalibrierung,
Mehrfachauswahl im Editor, weitere Achsen) und der größte Teil von Phase 4
(siehe unten).

## Offen aus früheren Phasen

- [ ] **Test mit dem echten Fredorch F21S**: Gerät in Intiface Central finden
      (Bluetooth-Name `YXlinksSPP`), Zeitversatz und Höchsttempo einstellen.
      Wartet, bis das Gerät ausgepackt ist.
- [x] **Rückmeldung zur Grafikkarte**: VAAPI läuft auf Nobara (RX 5700 XT,
      „Video dekodieren“ ~96 % während der Analyse).
- [ ] **Tracking und Kalibrierung an echten Videos** prüfen und nachschärfen.

## Phase 4 (0.5.0)

### Genaueres Tracking

Beobachtung: Nach einiger Zeit wandern die Kästchen ab, einmal landete eines
auf der anderen Bildseite.

- [x] **Drehbare Kästchen**: Bereiche lassen sich beim Markieren drehen, und
      beim Verfolgen werden Drehung und Größe mitgeschätzt (Ähnlichkeits-
      transformation aus den Punkten mit RANSAC statt nur Median-
      Verschiebung). Damit folgen sie auch schräg liegenden oder sich
      drehenden Körperteilen.
- [x] **Drift-Korrektur**: Der Bereich wird regelmäßig mit seinem Aussehen
      beim Markieren verglichen (Template-Abgleich in der Umgebung) und
      zurückgezogen, wenn er abwandert.
- [x] **Plausibilitätsprüfung**: Unmögliche Sprünge (z.B. quer übers Bild
      innerhalb eines Bildes) werden verworfen. Ist ein Bereich verloren,
      bleibt er stehen statt zu raten.
- [x] **Verlust sichtbar machen**: Unsichere oder verlorene Stellen werden
      in der Kurve markiert, mit Meldung „Bereich 2 ab 1:23 verloren“.
- [x] **Nachjustieren**: Innerhalb eines Abschnitts ein Kästchen an späterer
      Stelle neu ziehen (Stützpunkt), ohne einen neuen Abschnitt anzufangen.
      Das Tracking verfolgt dann von dort aus weiter, die Abweichung davor
      wird rückwärts bis zum vorigen Stützpunkt ausgeglichen.

- [x] **Kästchen nachträglich ändern** (0.5.1): am Abschnitt-Start
      verschieben, Größe ändern, drehen; „Alle löschen“ zum Neuanfang.

- [x] **Genauer verfolgen** (0.6): Der Vergleich mit dem Aussehen dreht und
      skaliert mit dem Kästchen mit; jede Korrektur merkt sich ein weiteres
      Aussehen (bis zu 6 Vorlagen); ab jeder Korrektur wird auch rückwärts
      verfolgt und mit dem Vorwärts-Ergebnis überblendet. Verrutschte Kästchen
      lassen sich an jeder Stelle direkt anpacken und hinschieben.
- [x] **Player: Countdown und ruhigen Anfang überspringen** (0.6): Start 10 s
      bevor das Script losgeht, die letzten 10 s zählt ein Countdown herunter.

- [x] **Bearbeiten** (0.7): Tempo-Farbe in der Hub-Leiste neben dem Video,
      Abschnitt-Anfang/-Ende in der Kurve ziehen, Lesezeichen/Kapitel,
      Rhythmus-Vorlagen (gleichmäßig, schneller/langsamer, Doppelschlag,
      Wellen), Video schließen.

- [x] **Layout wie ein Schnittprogramm** (0.8): links Analyse/Tracking bzw.
      Playlist, Mitte Video, rechts Script, unten eine Zeitleiste über die
      ganze Breite mit Spuren (Video, Tempo, Kurve). Leisten einklappbar
      (Strg+1/Strg+2), Analyse-Optionen eingeklappt.

### Stapel mit Tracking

Problem: Ohne Markierung weiß die App nicht, was sie verfolgen soll.

- [x] **Vorbereiten, dann durchlaufen lassen**: In einer Liste die Videos
      nacheinander öffnen, Bereiche, Abschnitte und Kalibrierung festlegen,
      ein paar Sekunden pro Video. Gespeichert wird das neben dem Video
      (`<Video>.vtf.json`). Danach rechnet der Stapel alle Videos am Stück,
      z.B. über Nacht. Videos ohne Vorbereitung laufen wie bisher mit
      „Bewegung im Bild“.
- [ ] *Später, optional*: automatische Erkennung der Körperteile mit einem
      Haltungsmodell (Pose-Estimation). Aufwendiger, braucht ein
      zusätzliches Modell und mehr Rechenzeit.

### Weitere Ideen

- [x] **Heatmap**: farbige Leiste unter der Zeitleiste, wo Hübe schnell oder
      langsam sind.
- [x] **Tempo-Grenze beim Erzeugen**: Hübe, die das Gerät nicht schafft (F21S:
      voller Hub unter ca. 200 ms), schon im Script begrenzen, nicht erst
      beim Abspielen.
- [x] **Muster-Steuerung** (0.9): über 50 fertige Muster mit Vorschaubild,
      eigene Muster aus Bausteinen (Hub, Fahren, Halten, mit Verlauf),
      Tiefe und Tempo dauerhaft gespeichert und live änderbar (Maus, Tasten,
      Controller über /dev/input/js*). Muster auch beim Rhythmus-Einfügen.
- [x] **Abläufe** (0.9): Schritte mit Muster (ganz oder Teil), Dauer, Tempo
      und Tiefe, weiche Übergänge, Schleife; Vorlagen bis zu einer Stunde.
      Mehr Muster (56).

## Phase 5 (0.10.0)

- [x] **Handy-Fernbedienung**: Webseite im Heimnetz mit PIN (nur Python-Standardbibliothek),
      Start/Stopp, Not-Aus, Muster/Ablauf/Zufall, Tiefe, Tempo, Schritte.
- [x] **Einrichtungs-Assistent**: Verbinden, Richtung, Hubbereich (fährt live
      an die Grenze), Höchsttempo in Stufen, Verzögerung mit Blink-Takt und Klick.
- [x] **Sicherheits-Timer** (abschaltbar, standardmäßig aus): Höchstdauer, Stopp
      ohne Bedienung, Pausen mit automatischem Weiter.
- [x] **Musik-Modus**: Schlagerkennung live vom Systemton (parec/pw-record) oder
      Mikrofon, Tempo-Schätzung und Vorhersage. **Script aus Musik** für die
      Tonspur eines Videos.
- [x] **Andere Player**: mpv (IPC), VLC (Weboberfläche), DeoVR und HereSphere
      (DeoVR-Fernsteuerung), Script neben dem Video oder im Script-Ordner.
- [x] **Zufallsgenerator**: Stimmung, Bereiche, Wechselzeit, neu gewürfelt bei jedem Start.
- [x] **Andere Geräte**: Vibratoren über Intiface (ScalarCmd, Stärke aus Tempo
      oder Tiefe), TCode-Geräte (OSR2/SR6) über USB oder UDP mit allen sechs Achsen.

## Für später

- [ ] **Automatisches Tracking per KI (Pose-Erkennung)**: Die App findet
      Körperteile selbst, statt dass man Kästchen zieht. Dafür braucht es
      **keine API und keine große KI**: Haltungsmodelle wie MediaPipe Pose,
      RTMPose oder MoveNet sind klein (etwa 5–30 MB), laufen lokal auf der CPU
      mit 10–30 Bildern pro Sekunde und lassen sich über OpenCV (ONNX)
      einbauen. Das AppImage würde dadurch etwas größer. Offene Punkte: Die
      Modelle sind auf normale Körperhaltungen trainiert, bei Nahaufnahmen
      und verdeckten Körpern werden sie ungenau. Deshalb eher als Vorschlag für
      die Kästchen, die man dann korrigiert. Lizenzen prüfen: MediaPipe und
      RTMPose Apache-2.0 (gut), YOLO-Pose AGPL (ungünstig).
- [ ] **Funscript-Bibliothek**: alle Scripts eines Ordners mit Heatmap-Vorschau,
      Suche und Bewertung, Doppelklick spielt ab.
- [ ] **Script-Qualitätsprüfung**: warnt vor zu schnellen Stellen, Lücken,
      Zittern und Abschnitten ohne Bewegung, mit Sprung zur Stelle.
- [ ] **Sitzungs-Statistik**: Dauer, Hübe, Lieblingsmuster (nur lokal).
- [ ] **Browser-Videos** (Erweiterung oder Bookmarklet), um Scripts auch zu
      Videos im Browser zu spielen.
- [ ] **QR-Code** für die Handy-Fernbedienung statt Adresse abtippen.
