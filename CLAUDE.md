# Arbeitsweise mit Claude

Gilt für jede Sitzung in diesem Repository.

## Sprache
- Mit dem Nutzer auf Deutsch sprechen.
- In der App ist deutscher Text der Schlüssel: `tr("…", *args)`, `plural(n, eins, viele)`, `dec()`, `num()`
  aus `vtf.i18n`. Jede neue Zeichenkette braucht eine englische Übersetzung in `src/vtf/locale_en.py`
  (`tests/test_i18n.py` prüft das).

## Ablauf
- Arbeiten auf dem Sitzungs-Branch, dann einen PR öffnen und ihn beobachten (CI, Kommentare).
- PRs nicht als Entwurf anlegen, sondern gleich bereit zum Mergen (draft: false).
- **Der Nutzer merged.** Nicht selbst mergen, außer der Nutzer erlaubt es ausdrücklich für diesen PR.
- Nach einem Merge den Branch neu auf `origin/main` setzen, bevor weitergearbeitet wird.
- Kein Force-Push, kein Amend. Nur eigene temporäre Dateien löschen.
- Vor dem Push: `python3 -m pytest -q`.
- Neue Version: `src/vtf/__init__.py` und `pyproject.toml` gemeinsam hochsetzen.

## Release
- Nur nach ausdrücklichem OK des Nutzers (z. B. „darfst bauen, wenn gemerged ist“).
- Workflow `appimage.yml` per workflow_dispatch auf `main` mit `release: true`.
- Danach prüfen: `Video-to-Funscript-x86_64.AppImage` und `SHA256SUMS` vom Release laden,
  `sha256sum -c`, `--selftest <video> out.png` mit `VTF_LANG=de` und `en`
  (`QT_QPA_PLATFORM=offscreen APPIMAGE_EXTRACT_AND_RUN=1`), `--check-update`, aufräumen,
  dem Nutzer berichten.

## Stil
- Plugins stehen in `.claude/settings.json` (Ponytail, claude-mem, Headroom, task-observer, claude-code-setup).
- Ponytail-Regeln: kleinste vollständige Änderung, vorhandenen
  Code nutzen, keine ungefragten Extras, neue Logik bekommt einen kleinen Test. Am Ende der Antwort
  ein bis zwei Zeilen: was ausgelassen wurde, welches Risiko bleibt.
- Keine Modell-Namen in Commits oder PRs. Die E-Mail-Adresse des Nutzers nirgends eintragen.
- Bilder für Doku nur mit synthetischen Demo-Videos (`tools/screenshots`).
