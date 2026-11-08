# Video-to-Funscript Generator

Kostenlose, quelloffene Software zur **lokalen** Videoanalyse und automatischen
Erstellung von Funscripts. Keine Cloud, keine Abos, keine API-Kosten.

![Screenshot](docs/screenshot.png)

Entwickelt und getestet für Nobara Linux (KDE) mit AMD Ryzen 7 3700X und
Radeon RX 5700 XT – läuft aber auf jedem Linux mit Python ≥ 3.10.

## Funktionen (Stand 0.1)

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
   Kurve verschieben: freie Fläche ziehen (oder mittlere Maustaste), die
   Scrollleiste darunter, Umschalt+Mausrad oder waagrecht wischen (Touchpad).
   Video-Zeitleiste: Klick springt direkt an die Stelle und lässt sich gleich
   weiterziehen, Mausrad = 1 s vor/zurück.
6. **Strg+S** speichert `<Video>.funscript` neben dem Video – so finden es
   Player automatisch.

**Ton**: Beim Abspielen läuft der Ton des Videos mit, synchron zum Bild und
zum Gerät. Lautsprecher-Knopf oder **M** schaltet ihn stumm, der Regler daneben
stellt die Lautstärke ein. Beides merkt sich die App.

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

1. FFmpeg dekodiert das Video verkleinert in Graustufen (optional per VAAPI).
2. Für jedes Bildpaar berechnet OpenCV den dichten optischen Fluss
   (Farnebäck). Die globale Kamerabewegung (Median) wird abgezogen und aus den
   am stärksten bewegten Pixeln ein Bewegungsvektor gebildet.
3. Die Geschwindigkeit wird auf die gewählte Achse projiziert und zu einer
   Position aufsummiert, Drift wird entfernt, geglättet und normalisiert.
4. Hoch- und Tiefpunkte (mit Hysterese = Empfindlichkeit) werden zu
   Funscript-Punkten.

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
