import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from videogen import make_video  # noqa: E402


@pytest.fixture
def sine_video(tmp_path):
    return make_video(tmp_path / "sine.avi")
