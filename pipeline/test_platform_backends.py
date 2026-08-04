import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("pipeline_run", Path(__file__).with_name("run.py"))
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def test_select_video_encoder_uses_videotoolbox_only_on_macos():
    encoders = " V..... h264_videotoolbox\n V..... libx264\n"
    assert R.select_video_encoder(encoders, platform_name="darwin") == "h264_videotoolbox"
    assert R.select_video_encoder(encoders, platform_name="linux") == "libx264"


def test_select_video_encoder_fails_when_no_supported_encoder_exists():
    with pytest.raises(RuntimeError, match="H.264 encoder"):
        R.select_video_encoder(" V..... vp9\n", platform_name="linux")


def test_select_asr_backend_defaults_by_platform():
    assert R.select_asr_backend(platform_name="darwin") == "mlx"
    assert R.select_asr_backend(platform_name="linux") == "whisper"


def test_find_japanese_font_returns_first_existing_candidate(tmp_path):
    missing = tmp_path / "missing.ttf"
    available = tmp_path / "NotoSansCJK-Bold.ttc"
    available.write_bytes(b"font")
    assert R.find_japanese_font([str(missing), str(available)]) == str(available)


def test_api_transcribe_sends_environment_key_without_logging_it(tmp_path, monkeypatch):
    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"wav")
    wav.with_suffix(".mp3").write_bytes(b"mp3")
    seen = {}

    class Result:
        stdout = json.dumps({"text": "ok"})

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Result()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-key")
    monkeypatch.setattr(R.subprocess, "run", fake_run)

    result = R._api_transcribe(str(wav), "whisper-1")

    assert result == {"text": "ok"}
    assert "Authorization: Bearer test-secret-key" in seen["cmd"]
    assert "***" not in " ".join(seen["cmd"])
