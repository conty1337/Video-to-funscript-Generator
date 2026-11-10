# Roadmap

Stand: 0.8.4. Erledigt sind Phase 1 (Analyse, Editor, Export, Stapel),
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
- [ ] **Andere Player**: Scripts auch zu Videos aus anderen Playern (Browser,
      VLC) auf dem Gerät abspielen.
