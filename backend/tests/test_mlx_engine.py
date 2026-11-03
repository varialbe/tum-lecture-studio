import sys
import types

import tqdm

from lecture_studio.stt.base import TranscribeOptions
from lecture_studio.stt.engines import MlxWhisperEngine


def test_mlx_engine_hooks_tqdm_despite_shadowed_submodule(monkeypatch, tmp_path):
    # Mirror the real package: `from .transcribe import transcribe` in __init__
    # makes `mlx_whisper.transcribe` the function, not the submodule.
    sub = types.ModuleType("mlx_whisper.transcribe")
    sub.tqdm = tqdm

    def transcribe(path, **kw):
        with sub.tqdm.tqdm(total=100, disable=True) as pbar:
            pbar.update(50)
            pbar.update(50)
        return {"language": "de", "segments": [{"start": 0.0, "end": 1.5, "text": " Hallo"}]}

    sub.transcribe = transcribe
    pkg = types.ModuleType("mlx_whisper")
    pkg.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", pkg)
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", sub)

    progress: list[float] = []
    orig = tqdm.tqdm
    res = MlxWhisperEngine("repo", "mlx-large-v3-turbo").transcribe(
        tmp_path / "a.wav", TranscribeOptions(), progress.append
    )
    assert tqdm.tqdm is orig
    assert progress[:2] == [0.5, 1.0]
    assert res.language == "de"
    assert [s.text for s in res.segments] == ["Hallo"]
