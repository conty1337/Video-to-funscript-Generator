# Anleitung: vom Video zum Script

Kurz und der Reihe nach. Die ausführliche Beschreibung aller Funktionen steht
in der [README](../README.md).

![Tracking: Kästchen markieren, gezoomt, mit Optionen](tracking.png)

## Aufbau des Fensters

- **Links**: Analyse. Methode, Tracking-Abschnitte, Optionen, Knopf
  „Analysieren“. Im Player steht hier die Playlist.
- **Mitte**: das Video, rechts daneben die Hub-Leiste. Sie zeigt die
  Script-Position, ihre Farbe zeigt das Tempo.
- **Rechts**: Script-Einstellungen. Sie wirken sofort auf die Kurve.
- **Unten**: die Zeitleiste mit Video, Tempo und der Hub-Kurve zum Bearbeiten.

Strg+1 klappt die linke Leiste ein, Strg+2 die rechte.

## In 8 Schritten zum Script

1. **Video öffnen**: Strg+O oder die Datei ins Fenster ziehen.
2. **Methode wählen** (links):
   - *Bewegung im Bild*: erkennt die stärkste Bewegung selbst. Schnell, gut
     bei ruhiger Kamera und einer klaren Bewegung.
   - *Tracking*: du markierst, was verfolgt wird. Genauer, empfohlen.
3. **Markieren** (bei Tracking): an eine Stelle spulen, wo alles gut zu sehen
   ist, und Kästchen ins Video ziehen.
   - **Kästchen 1** auf das Teil, das sich bewegt.
   - **Kästchen 2** (und 3) auf ein Gegenstück, das stillhält oder sich
     anders bewegt. Gemessen wird der Abstand zwischen 1 und den anderen,
     deshalb stört die Kamera dann kaum.
   - Stellen mit **Muster oder Kanten** nehmen, keine glatten Flächen. Kein
     Hintergrund und nichts, was später verdeckt wird, mit ins Kästchen.
   - Kleine Stellen: **Strg+Mausrad** zoomt ins Video, mit gedrücktem Mausrad
     den Ausschnitt verschieben, Strg+0 setzt zurück.
   - Kästchen anpassen: innen ziehen verschiebt, eine Ecke ändert die Größe,
     der runde Griff oder das Mausrad drehen.
4. **Kalibrieren** (empfohlen): zu einer Stelle spulen, wo es *ganz drin* ist,
   und **Ganz drin** (D) drücken, dann zu *ganz draußen* und **Ganz draußen**
   (A). Dann nutzt das Script die echte Tiefe.
5. **Szenenwechsel**: dort **Ende hier** (E) drücken und an der neuen Stelle
   neue Kästchen ziehen. Damit beginnt ein neuer Abschnitt.
6. **Analysieren**: der orange Knopf unten links.
7. **Prüfen und korrigieren**: abspielen und schauen, ob die Kästchen
   mitlaufen.
   - **Rot** in der Kurve: Hier war ein Bereich verdeckt oder verloren, dort
     entstehen keine Hübe.
   - Ein Kästchen liegt daneben: an der Stelle **anpacken und richtig
     hinschieben**, dann neu analysieren. Die Zeit davor wird mit ausgeglichen.
8. **Speichern**: Strg+S legt `<Video>.funscript` neben das Video.

## Optionen (links unter „Optionen“)

| Option | Wann |
|---|---|
| Auflösung | 640 px (Standard): genauer, gut für kleine Kästchen. Kleiner ist schneller |
| Kamerabewegung ausgleichen | an lassen, wichtig bei nur einem Kästchen |
| Kästchen drehen und skalieren mit | an für Körper, **aus für Maschinen** und starre Teile |
| Bereich 2 und 3 fest verankern | an, wenn 2/3 auf festen Teilen sitzen und die Kamera ruhig ist. Dann kann nichts sie wegziehen. Bei Kamerafahrten aus |

## Script feinstellen (rechts)

| Einstellung | Wirkung |
|---|---|
| Richtung | welche Bewegung im Bild zum Hub wird (bei 2 Kästchen egal) |
| Empfindlichkeit | höher = auch kleine Hübe |
| Glättung | bügelt Zittern aus |
| Mindestabstand | kürzeste Zeit zwischen zwei Punkten |
| Drift-Filter | entfernt langsames Wegwandern |
| Normalisierung | streckt leise Stellen auf vollen Hub (bei Kalibrierung egal) |
| Tempo-Grenze | kürzt zu schnelle Hübe. F21S: etwa 500 /s |
| Hubbereich | kleinster und größter Wert, z.B. 10 bis 90 |

Von Hand nachbessern: Punkte in der Kurve ziehen, Doppelklick fügt hinzu,
Rechtsklick löscht. **Strg+T** fügt einen Rhythmus oder ein Muster aus der
Muster-Steuerung für einen Bereich ein.

## Abspielen

- **Gerät**: Intiface Central starten → „Start Server“ → in der App
  **Gerät** (Strg+D) → Verbinden. Ohne Gerät: „Simulation“.
- **Player** (Strg+P): Videos in die Playlist ziehen, „Playlist abspielen“.
  Ein ruhiger Anfang wird übersprungen, vor dem Start zählt ein Countdown.
- **Muster** (Strg+M): fertige oder eigene Muster ohne Video auf dem Gerät
  abspielen. Nur zwei Regler, **Tiefe** und **Tempo**. Sie bleiben gespeichert
  und lassen sich beim Abspielen ändern: ↑/↓ Tiefe, ←/→ Tempo, Leertaste
  Start/Stopp. Ein Controller (Xbox/PlayStation) geht auch: linker Stick
  Tiefe, rechter Stick Tempo, A Start/Stopp, B Stopp, LB/RB Muster wechseln.
  Eigene Muster baust du aus Bausteinen (Neu bzw. „Als Kopie ändern“), das
  Bild zeigt sofort, wie es aussieht.
- **Abläufe** (oben links umschalten): mehrere Muster hintereinander, jedes mit
  eigener Dauer, eigenem Tempo und eigener Tiefe, z.B. eine Stunde vom
  langsamen Start bis zum Ende. Pro Schritt lässt sich mit „Teil“ auch nur ein
  Stück eines Musters nehmen.
- **Esc** stoppt das Gerät sofort.

## Wenn etwas nicht passt

| Problem | Lösung |
|---|---|
| Kästchen wandert langsam weg | Stelle mit mehr Muster markieren, an der Stelle anpacken und zurückschieben |
| Kästchen wird schief oder lang | „Kästchen drehen und skalieren mit“ ausschalten |
| Hand oder Gegenstand zieht Kästchen 2/3 weg | „Bereich 2 und 3 fest verankern“ einschalten |
| Zu viele kleine Zitter-Hübe | Empfindlichkeit runter, Glättung rauf |
| Hübe zu flach | kalibrieren (D/A) oder Normalisierung nutzen |
| Kurve läuft nicht mit | nach dem Scrollen 3 s warten, sie folgt wieder |
| Analyse langsam | Grafikkarte prüfen: Hilfe → „Dekodierung testen…“ |

## Tastenkürzel

| Taste | Funktion |
|---|---|
| Leertaste | Abspielen/Pause |
| ← / → | Bild zurück/vor, mit Umschalt 1 s |
| E / D / A | Abschnitt-Ende, ganz drin, ganz draußen |
| B, Alt+← / Alt+→ | Lesezeichen setzen, voriges/nächstes |
| Strg+Mausrad, Strg+0 | Video zoomen, Zoom zurücksetzen |
| Strg+T | Rhythmus einfügen |
| Strg+S | Script speichern |
| Strg+W | Video schließen |
| Strg+P | Player |
| Strg+M | Muster-Steuerung |
| Strg+1 / Strg+2 | linke/rechte Leiste ein/aus |
| F11 | Vollbild |
| Esc | Not-Aus (Gerät stoppen) |
