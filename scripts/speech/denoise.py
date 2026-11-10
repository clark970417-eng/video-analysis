"""Run the official local DeepFilterNet3 binary on decoded audio."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import wave

import numpy as np


CACHE = Path.home() / "Library" / "Caches" / "codex-transcribe-media"
FILTER = CACHE / "deepfilternet-0.5.6"


def has_loud_steady_noise(audio):
    """Conservative whole-clip gate for a persistent, fan-like noise floor."""
    frame_size = 4096  # About 0.26 seconds at the transcript sample rate.
    count = len(audio) // frame_size
    if count < 8:
        return False
    frames = audio[:count * frame_size].reshape(count, frame_size)
    rms = np.sqrt(np.einsum("ij,ij->i", frames, frames) / frame_size)
    levels = 20 * np.log10(np.maximum(rms, 1e-8))
    floor, quiet_limit, peak = np.percentile(levels, [20, 40, 90])
    # A loud bed should be close in level to the loudest parts of the recording.
    if floor < -38 or peak - floor > 3:
        return False

    quiet = np.flatnonzero(levels <= quiet_limit)
    if len(quiet) > 128:
        quiet = quiet[np.linspace(0, len(quiet) - 1, 128, dtype=int)]
    power = np.abs(np.fft.rfft(frames[quiet] * np.hanning(frame_size))) ** 2
    bands = power[:, 1:].reshape(-1, 32, 64).sum(axis=2)
    bands /= np.maximum(bands.sum(axis=1, keepdims=True), 1e-12)
    profile = np.median(bands, axis=0)
    similarity = np.sqrt(bands * profile).sum(axis=1)
    # Reject isolated tones and noise floors whose spectral shape keeps changing.
    return profile.max() < 0.9 and np.percentile(similarity, 20) >= 0.94


def write_wav(path, audio, sample_rate):
    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2")
    with path.open("xb") as output, wave.open(output, "wb") as wav:
        wav.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm.tobytes())


def denoise(media, decode_audio):
    return denoise_samples(decode_audio(str(media), sampling_rate=48000), decode_audio)


def denoise_samples(audio, decode_audio):
    binary = FILTER / "deep-filter"
    model = FILTER / "DeepFilterNet3_onnx.tar.gz"
    if not binary.is_file() or not model.is_file():
        raise RuntimeError(f"DeepFilterNet3 is missing from {FILTER}. See the skill setup instructions.")

    sample_rate = 48000
    with TemporaryDirectory(prefix="transcribe-denoise-") as directory:
        source = Path(directory) / "audio.wav"
        output = Path(directory) / "cleaned"
        # The CLI trims the filter delay and skips the last partial hop.
        write_wav(source, np.pad(audio, (0, 4800)), sample_rate)
        result = subprocess.run(
            [str(binary), "--model", str(model), "--compensate-delay",
             "--atten-lim-db", "18", "--output-dir", str(output), str(source)],
            capture_output=True, text=True,
        )
        cleaned = output / source.name
        if result.returncode or not cleaned.is_file():
            raise RuntimeError(f"DeepFilterNet3 failed: {(result.stderr or result.stdout).strip()}")
        return decode_audio(str(cleaned), sampling_rate=16000)[:round(len(audio) / 3)]
