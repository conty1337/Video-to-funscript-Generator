"""Fetch the AI models from OpenMMLab, check them and put them into a folder,
for the GitHub copy (release "ki-modelle", see .github/workflows/models.yml).

    python tools/mirror_models.py OUT_DIR
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vtf import pose  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    last = [0.0]

    def progress(p: float, name: str) -> None:
        if time.monotonic() - last[0] > 10 or p >= 1:
            last[0] = time.monotonic()
            print(f"{p * 100:5.1f} %  {name}", flush=True)

    pose.download(progress, mirror=False, folder=out)
    for m in pose.MODELS:
        f = out / m.name
        print(f"{m.name}: {f.stat().st_size / 1e6:.1f} MB, sha256 {m.sha256}")


if __name__ == "__main__":
    main()
