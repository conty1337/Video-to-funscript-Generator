# AppImage-Runtime

`runtime-x86_64` ist die statische type2-Runtime des AppImage-Projekts
(https://github.com/AppImage/type2-runtime, MIT-Lizenz, enthält squashfuse und
libzstd unter ihren jeweiligen Lizenzen). Sie steht vor dem SquashFS-Abbild der
App und hängt die Datei beim Start ein. FUSE 2 wird dafür nicht gebraucht.

Sie liegt fest im Repository, damit der Build nicht von Downloads bei
GitHub-Releases abhängt und reproduzierbar bleibt.

- Stand: continuous-Build vom 2026-11-08
- SHA-256: `617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46`

Aktualisieren: neue Datei von den Releases des type2-runtime-Projekts laden,
ersetzen, Prüfsumme hier und in `scripts/build-appimage.sh` eintragen.
