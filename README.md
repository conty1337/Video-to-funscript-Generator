# Video-to-Funscript Generator

Kostenlose, quelloffene Software zur **lokalen** Videoanalyse und automatischen
Erstellung von Funscripts. Keine Cloud, keine Abos, keine API-Kosten.

![Screenshot](docs/screenshot.png)

Entwickelt und getestet für Nobara Linux (KDE) mit AMD Ryzen 7 3700X und
Radeon RX 5700 XT – läuft aber auf jedem Linux mit Python ≥ 3.10.

## Funktionen (Stand 0.4)

| | Funktion | Status |
|---|---|---|
| 1 | Grafische Oberfläche (PySide6) | ✅ |
| 2 | Videovorschau mit Abspielen, Frame-Schritt, Suchleiste | ✅ |
| 3 | Automatische Bewegungsanalyse (dichter optischer Fluss) | ✅ |
| 4 | Bewegungsrichtung (vertikal / horizontal / automatisch) und Bewegungsumfang | ✅ |
| 5 | Einstellbare Empfindlichkeit, Glättung, Mindestabstand, Drift-Filter | ✅ (Live-Vorschau) |
| 6 | Analysekurve anzeigen und Punkte bearbeiten (inkl. Rückgängig) | ✅ |
| 7 | Funscript-Export (`<Video>.funscript` neben dem Video) | ✅ |
| 8 | Stapelverarbeitung ganzer Ordner (GUI + Kommandozeile) | ✅ |
| 9 | Bereits analysierte Videos überspringen | ✅ |
| 10 | Gerätesteuerung (Fredorch F21S über Intiface Central, Simulation) | ✅ Teil 1, Test mit echtem Gerät steht aus |
| 11 | Ton bei der Wiedergabe | ✅ |
| 12 | Player-Modus mit Playlist | ✅ |
| 13 | Tracking: markierte Körperteile/Objekte verfolgen, Abschnitte, Tiefen-Kalibrierung | ✅ |
| 14 | Editor: Mehrfachauswahl, Kopieren/Einfügen, Abschnitt glätten/verstärken | ✅ |
| 15 | Weitere Achsen (Surge, Sway, Twist, Roll, Pitch) | ✅ |

## Installation als AppImage (am einfachsten)

Eine Datei, keine Installation, Python wird nicht benötigt:

1. Die neueste Version herunterladen:
   [Video-to-Funscript-x86_64.AppImage](https://github.com/conty1337/Video-to-funscript-Generator/releases/latest/download/Video-to-Funscript-x86_64.AppImage)
   (oder unter **Releases**).
2. Ausführbar machen und starten:
   ```bash
   chmod +x Video-to-Funscript-*.AppImage
   ./Video-to-Funscript-*.AppImage
   ```
   Oder in Dolphin: Rechtsklick → Eigenschaften → Berechtigungen → „Ausführbar“.
3. Beim ersten Start trägt sich die App selbst ins Startmenü ein (abschaltbar
   unter Hilfe → „Im Startmenü anzeigen“). Am besten legst du die Datei vorher
   an ihren festen Platz, z. B. `~/Applications`. Wird sie verschoben, passt
   der nächste Start den Eintrag an. Wer **Gear Lever** nutzt, bekommt keinen
   doppelten Eintrag.

**Updates kommen automatisch:** Beim Start sucht die App nach einer neuen
Version, lädt sie im Hintergrund, prüft sie per SHA-256 und ersetzt die
AppImage-Datei. Der nächste Start oder „Neu starten“ in der Statusleiste nutzt
dann die neue Version. Manuell: Hilfe → „Nach Updates suchen“, abschaltbar
über Hilfe → „Automatisch aktualisieren“. Dafür muss der Ordner mit dem
AppImage beschreibbar sein (z. B. `~/Applications`, wie bei Gear Lever).

FFmpeg und die OpenCL-Treiber kommen weiterhin vom System (siehe unten).
Ohne FFmpeg dekodiert das AppImage über das mitgelieferte OpenCV.

## Installation aus dem Quellcode (Nobara / Fedora)

```bash
sudo dnf install git python3 ffmpeg          # ffmpeg ist bei Nobara meist schon da
git clone https://github.com/conty1337/Video-to-funscript-Generator.git
cd Video-to-funscript-Generator
./install.sh
```

`install.sh` legt eine virtuelle Umgebung `.venv` an, installiert NumPy, OpenCV
und PySide6, erstellt einen **Startmenü-Eintrag** und die Befehle `vtf-gui` und
`vtf` in `~/.local/bin`.

Starten: Startmenü → „Video-to-Funscript Generator“, oder `./start.sh`.

### GPU (RX 5700 XT)

Die Software nutzt die Grafikkarte auf zwei Wegen, beides optional:

* **Hardware-Dekodierung (VAAPI)** über FFmpeg – funktioniert mit dem
  Mesa-Treiber von Nobara direkt. Einstellung „Hardware-Dekodierung“.
* **OpenCL** für den optischen Fluss (OpenCV T-API). Dafür eine
  OpenCL-Laufzeit installieren, z. B.
  `sudo dnf install mesa-libOpenCL` (Mesa rusticl, wird von `start.sh` mit
  `RUSTICL_ENABLE=radeonsi` aktiviert) oder `rocm-opencl`. Danach in der GUI
  „GPU (OpenCL)“ anhaken. `vtf info` zeigt, ob ein Gerät gefunden wurde.

Hinweis: Bei der Standard-Analyseauflösung (320 px) ist die CPU (8 Kerne) oft
genauso schnell wie OpenCL, weil das Kopieren zur GPU Zeit kostet. Bei höheren
Auflösungen lohnt sich OpenCL eher.

## Bedienung

1. Video öffnen (Strg+O oder per Drag & Drop).
2. Optional: im Vorschaubild ein Rechteck um den relevanten Bereich ziehen
   (ROI). Rechtsklick entfernt die ROI. Das verbessert die Erkennung deutlich.
3. **Analysieren** klicken. Die Rohdaten werden zwischengespeichert
   (`~/.cache/video-to-funscript`), eine erneute Analyse desselben Videos mit
   denselben Einstellungen ist sofort fertig.
4. Script-Einstellungen anpassen – die Kurve aktualisiert sich sofort:
   * **Empfindlichkeit**: höher = auch kleine Bewegungen werden zu Punkten.
   * **Glättung**: entfernt Zittern.
   * **Mindestabstand**: kleinster zeitlicher Abstand zwischen zwei Punkten.
   * **Drift-Filter**: entfernt langsames Wegdriften der Position.
   * **Normalisierung**: Fenster, in dem der Hub auf den vollen Bereich
     skaliert wird (0 = über das ganze Video).
   * **Bewegungsumfang**: minimale/maximale Position (0–100).
   * **Umkehren** / **Immer voller Hub**.
5. Punkte bearbeiten: ziehen = verschieben, Doppelklick = neuer Punkt,
   Rechtsklick oder Entf = löschen, Mausrad = Zoom, Klick in die Kurve = zu
   dieser Stelle springen. Strg+Z / Strg+Umschalt+Z.
   **Mehrere Punkte**: Umschalt- oder Strg+Ziehen auf freier Fläche zieht ein
   Auswahlrechteck, Strg+Klick schaltet einzelne Punkte um, Strg+A wählt alle.
   Ausgewählte Punkte werden zusammen verschoben. Strg+X/C/V schneidet aus,
   kopiert und fügt an der Abspielposition ein (auch in ein anderes Video),
   Strg+G glättet die Auswahl, Strg+↑/↓ verstärkt oder schwächt den Hub.
   Kurve verschieben: freie Fläche ziehen (oder mittlere Maustaste), die
   Scrollleiste darunter, Umschalt+Mausrad oder waagrecht wischen (Touchpad).
   Video-Zeitleiste: Klick springt direkt an die Stelle und lässt sich gleich
   weiterziehen, Mausrad = 1 s vor/zurück.
6. **Strg+S** speichert `<Video>.funscript` neben dem Video – so finden es
   Player automatisch.

**Ton**: Beim Abspielen läuft der Ton des Videos mit, synchron zum Bild und
zum Gerät. Lautsprecher-Knopf oder **M** schaltet ihn stumm, der Regler daneben
stellt die Lautstärke ein. Beides merkt sich die App.

**Tracking** (genauer bei unruhigen Videos): In den Analyse-Einstellungen
„Methode: Tracking“ wählen, an eine Stelle spulen, wo alles gut zu sehen ist,
und im Video bis zu 3 Bereiche aufziehen, z.B. um die beiden Körperteile.
Jeder Bereich wird mit vielen Bildpunkten verfolgt.
* 1 Bereich: dessen Bewegung (Kamerabewegung wird abgezogen).
* 2–3 Bereiche: Bereich 1 relativ zu den anderen. Dann spielt die Kamera keine
  Rolle mehr, auch Zoom und Schwenks stören kaum.

*Abschnitte*: Das Markieren startet einen Abschnitt an dieser Stelle. **Ende
hier** (Taste E) beendet ihn, z.B. wenn die Szene vorbei ist. Nach einem
Szenenwechsel einfach an der neuen Stelle wieder markieren, dann beginnt dort
ein neuer Abschnitt. Nur in Abschnitten entstehen Punkte. Die Abschnitte stehen
als Liste in den Einstellungen (Klick springt zum Start) und als helle Bänder in
der Kurve. Rechtsklick ins Video löscht den Abschnitt an dieser Stelle.

*Tiefen-Kalibrierung*: Zu einer Stelle spulen, wo es ganz drin ist, und **Ganz
drin** (Taste D) klicken, dann zu einer Stelle ganz draußen und **Ganz
draußen** (Taste A). Ab dann nutzt das Script in diesem Abschnitt den echten
Abstand der Bereiche: ganz drin = 0, ganz draußen = 100, kurze Hübe bleiben kurz.
Ohne Kalibrierung wird jeder Hub auf den vollen Bereich gestreckt. Die
Kalibrierung wirkt sofort, ohne neue Analyse.

Beim Abspielen zeigt die Vorschau, wo die Bereiche gerade sind.

**Weitere Achsen**: Über der Kurve wählst du die Achse: Hub (L0), Surge (L1),
Sway (L2), Twist (R0), Roll (R1) oder Pitch (R2). Jede Achse hat eigene Punkte
und wird beim Speichern als `<Video>.roll.funscript` usw. neben dem Video
abgelegt, wie es Mehrachsen-Player (MultiFunPlayer, OFS) erwarten.
Bearbeiten → „Weitere Achsen aus Analyse erzeugen“ berechnet Sway aus der
seitlichen Bewegung und Roll aus dem Winkel zwischen den Tracking-Bereichen 1
und 2. Twist und Pitch setzt du von Hand. Der Fredorch F21S hat nur eine Achse,
das Gerät folgt deshalb immer „Hub“.

**Player-Modus**: Oben auf **Player** (Strg+P). Die Seitenleiste zeigt dann die
Playlist: Videos oder Ordner mit + hinzufügen oder hineinziehen, per Ziehen
umsortieren, Entf entfernt. Abgespielt wird jeweils das Funscript neben dem
Video, auf dem verbundenen Gerät, eins nach dem anderen. Optionen:
wiederholen, zufällige Reihenfolge, Videos ohne Script überspringen. Bild ↑/↓
springt zum vorherigen/nächsten Video, F11 schaltet Vollbild (Esc beendet es,
zusammen mit dem Not-Aus). Playlists lassen sich als `.m3u` laden und
speichern, die letzte merkt sich die App.

**Stapelverarbeitung**: Datei → Stapelverarbeitung (Strg+B) oder einen Ordner
ins Fenster ziehen. Es werden die aktuellen Einstellungen verwendet. Videos, für
die schon ein `.funscript` existiert, werden übersprungen (abschaltbar).

## Gerät steuern (Phase 2)

Die App spielt das Script synchron zum Video auf einem Gerät ab. Die
Bluetooth-Verbindung übernimmt **[Intiface Central](https://intiface.com/central/)**
(kostenlos, Open Source, für Linux als AppImage). Es unterstützt den
**Fredorch F21S** über sein Protokoll „fredorch“, das Gerät meldet sich als
`YXlinksSPP`.

1. Intiface Central starten und auf **„Start Server“** klicken (Adresse
   `ws://127.0.0.1:12345`).
2. Gerät einschalten. Die Hersteller-App am Handy darf nicht verbunden sein,
   das Gerät erlaubt nur eine Verbindung.
3. In dieser App: **Gerät** (Toolbar oder Strg+D) → Art „Intiface Central“ →
   **Verbinden**. Die App startet die Gerätesuche, und das Gerät erscheint in der Liste.
4. Mit **Oben/Unten** testen, dann das Video abspielen: Das Gerät folgt dem Script.

Ohne Gerät: Art **„Simulation“**. Die Hubschiene neben dem Video zeigt mit
einer weißen Umrandung, wo das Gerät gerade wäre. Die Simulation bildet den
F21S nach: 16 Positionen, voller Hub frühestens in etwa 200 ms.

Einstellungen im Geräte-Dialog:

- **Zeitversatz**: gleicht die Bluetooth-Verzögerung aus. Positiv heißt, das Gerät
  bekommt seine Befehle früher. Startwert 0, typisch 50–150 ms.
- **Max. Tempo**: Hübe, die schneller wären, werden verkürzt, nicht verzögert.
  So bleibt das Gerät synchron und wird geschont.
- **Hubbereich**: welcher Teil des Gerätewegs genutzt wird (Tiefe).
- **Richtung umkehren**.

**Sicherheit:** Bei Pause, Spulen, Schließen und mit **Esc (Not-Aus)** stoppt
das Gerät sofort. Nach Start oder Spulen fährt es zuerst langsam zur aktuellen
Script-Position und folgt erst dann.

## Kommandozeile

```bash
vtf analyze video.mp4                      # erzeugt video.funscript
vtf analyze video.mp4 --sensitivity 70 --roi 0.2 0.1 0.6 0.8
vtf batch ~/Videos                         # ganzer Ordner, bereits analysierte überspringen
vtf batch ~/Videos --overwrite --opencl
vtf info                                   # FFmpeg/OpenCL prüfen
vtf analyze --help                         # alle Optionen
```

## AppImage selbst bauen

```bash
sudo dnf install squashfs-tools   # mksquashfs, meist schon installiert
./scripts/build-appimage.sh       # erzeugt dist/Video-to-Funscript-<Version>-x86_64.AppImage
```

Der Build braucht keine Downloads außer den Python-Paketen: Die AppImage-Runtime
liegt in `packaging/` (siehe [packaging/RUNTIME.md](packaging/RUNTIME.md)).

Die GitHub-Action `appimage` baut das AppImage bei jedem Push auf `main` und bei
jedem PR automatisch und testet es (Video analysieren, Screenshot).

**Release erstellen:** Version in `src/vtf/__init__.py` und `pyproject.toml`
erhöhen und mergen. Dann auf GitHub unter Actions → appimage → „Run workflow“
mit Haken bei „release“ auf `main` starten (oder einen Tag `v0.2.0` pushen).
Die Action legt den Tag an, bricht ab, wenn es die Version schon gibt, und veröffentlicht
`Video-to-Funscript-x86_64.AppImage` und `SHA256SUMS`. Installierte Versionen
aktualisieren sich daraufhin selbst.

## Wie die Analyse funktioniert

1. FFmpeg dekodiert das Video verkleinert in Graustufen. Mit VAAPI (AMD/Intel)
   dekodiert *und* verkleinert die Grafikkarte, zur CPU gehen nur die kleinen
   Analysebilder. Klappt das nicht (z.B. Codec von der Karte nicht unterstützt),
   wird automatisch auf CPU-Dekodierung zurückgefallen.
   Hilfe → „Dekodierung testen…“ zeigt, welcher Weg bei einem Video klappt und
   wie schnell. Die „GPU-Auslastung“ in Systemmonitoren zeigt meist nur die
   3D-Einheit; das Dekodieren läuft im Video-Block (VCN), sichtbar z.B. mit
   `amdgpu_top`. Das Tracking selbst rechnet die CPU.
2. Für jedes Bildpaar berechnet OpenCV den dichten optischen Fluss
   (Farnebäck). Die globale Kamerabewegung (Median) wird abgezogen und aus den
   am stärksten bewegten Pixeln ein Bewegungsvektor gebildet.
3. Die Geschwindigkeit wird auf die gewählte Achse projiziert und zu einer
   Position aufsummiert, Drift wird entfernt, geglättet und normalisiert.
4. Hoch- und Tiefpunkte (mit Hysterese = Empfindlichkeit) werden zu
   Funscript-Punkten.

Beim **Tracking** ersetzt Schritt 2 der optische Fluss nur innerhalb der
markierten Bereiche: Bis zu 60 Merkmalspunkte je Bereich werden mit
Lucas-Kanade verfolgt, eine Vorwärts-Rückwärts-Prüfung verwirft abgerutschte
Punkte, und der Bereich bewegt sich um den Median der übrigen.

Da Schritt 1–2 (teuer) von 3–4 (billig) getrennt sind, wirken
Script-Einstellungen sofort ohne neue Analyse.

## Gestaltung

Die Oberfläche folgt einer festen Gestaltungsrichtung (Schnittplatz bei Nacht,
ein Bernstein-Akzent nur für das Script). Details und Quellen stehen in
[docs/design.md](docs/design.md).

## Entwicklung

```bash
.venv/bin/pytest
```

Projektstruktur:

```
src/vtf/
  video.py       Video-Infos + schnelles Dekodieren (FFmpeg/OpenCV)
  analysis.py    Optischer Fluss -> Bewegungsdaten (+ Cache)
  generator.py   Bewegungsdaten -> Kurve -> Funscript-Punkte
  funscript.py   Dateiformat
  batch.py       Ordnerverarbeitung
  gpu.py         OpenCL-Hilfen
  device/        Gerätesteuerung: Simulation, Intiface (Buttplug), Synchronisation
  cli.py         Kommandozeile (vtf)
  gui/           PySide6-Oberfläche (vtf-gui), Gestaltung in gui/theme.py
```

## Lizenz

MIT – siehe [LICENSE](LICENSE).
