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
- [x] **Zufallsgenerator**: live gewürfelt pro Abschnitt, Stimmungen inkl. „Alles“, Bereiche und
      Wechselzeit beim Abspielen änderbar, Stimmung auch vom Handy.
- [x] **Andere Geräte**: Vibratoren über Intiface (ScalarCmd, Stärke aus Tempo
      oder Tiefe), TCode-Geräte (OSR2/SR6) über USB oder UDP mit allen sechs Achsen.

## Für später

- [x] **KI-Körpererkennung** (0.10.8): YOLOX-m + RTMW-l (OpenMMLab,
      Apache-2.0, ~310 MB, Download beim ersten Benutzen, läuft über OpenCV
      DNN). **KI-Vorschlag** setzt Kästchen auf Becken, Mund, Hände usw.,
      **KI-Kontrolle** holt weggerutschte Kästchen während der Analyse zurück
      und überbrückt längere Verdeckungen. Offen: Test an echten Videos,
      vielleicht GPU (OpenCL) für die Netze.
- [x] **KI-Gerüst zum Anklicken** (0.10.9): Punkte und Linien der KI im Video,
      ein Klick auf einen Punkt oder eine Linie setzt das Kästchen dort.
- [ ] **Hub direkt aus Körperpunkten** (im Hinterkopf, erst wenn Tests mit
      echten Videos zeigen, dass der Tracker die Hübe nicht gut trifft): Statt
      ein Kästchen zu verfolgen, misst die KI den Abstand oder Winkel zwischen
      gewählten Punkten (z.B. Hüfte zu Knie, Winkel des Oberschenkels) und
      macht daraus den Hub.
      - Vorteil: robust gegen Verdeckung, Licht und Verformung; wählbar über
        das Gerüst.
      - Nachteil: Die KI müsste jedes Bild ansehen, ein 10-Minuten-Video hätte
        etwa 30–45 min Analyse auf der CPU (vielleicht schneller mit GPU/OpenCL
        oder jedem zweiten Bild).
      - Nachteil: Die Punkte zittern um ein paar Pixel, kleine Hübe würden
        ungenauer. Denkbar als Mischung: Tracker für die feine Bewegung, KI für
        die Lage.
      - Getestet (0.10.13, zwei echte Solo-Nahaufnahmen mit Maschine, liegend,
        Kamera von unten): Das Körpermodell findet die Hüftpunkte dort kaum
        (Sicherheit fast immer unter der Schwelle, nur 1 % bzw. 13 % der
        Bilder), die Beckenmitte springt im Mittel 40 px (bei 640 px Breite)
        zwischen zwei Blicken 0,2 s auseinander. Für solche Nahaufnahmen taugt
        ein Hub aus Körperpunkten nicht; sinnvoll erst für Ganzkörper-Szenen.
        Toys und Maschinen erkennt das Modell ohnehin nicht. Eine KI-Abfrage
        kostet etwa 0,3 s (CPU, 640 px).
- [x] **Bildrand und kurze Verdeckungen** (0.10.8): Strecke außerhalb des
      Bildes und unter einer Hand wird weich überbrückt.
- [ ] **Funscript-Bibliothek**: alle Scripts eines Ordners mit Heatmap-Vorschau,
      Suche und Bewertung, Doppelklick spielt ab.
- [ ] **Script-Qualitätsprüfung**: warnt vor zu schnellen Stellen, Lücken,
      Zittern und Abschnitten ohne Bewegung, mit Sprung zur Stelle.
- [ ] **Sitzungs-Statistik**: Dauer, Hübe, Lieblingsmuster (nur lokal).
- [ ] **Browser-Videos** (Erweiterung oder Bookmarklet), um Scripts auch zu
      Videos im Browser zu spielen.
- [ ] **QR-Code** für die Handy-Fernbedienung statt Adresse abtippen.
