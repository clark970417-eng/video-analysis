# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["faster-whisper==1.2.1", "av==16.1.0", "praat-parselmouth==0.4.7"]
# ///

import argparse
import math
from pathlib import Path

import av
from faster_whisper.audio import decode_audio
import numpy as np
import parselmouth


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure pitch in a section of a local audio track.")
    parser.add_argument("media", type=Path)
    parser.add_argument("--start", type=float, default=0, help="Start time in seconds")
    parser.add_argument("--end", type=float, help="End time in seconds")
    args = parser.parse_args()

    if not math.isfinite(args.start) or args.start < 0:
        parser.error("--start must be a finite, nonnegative time")
    if args.end is not None and (not math.isfinite(args.end) or args.end <= args.start):
        parser.error("--end must be a finite time after --start")

    media = args.media.expanduser().resolve()
    if not media.is_file():
        parser.error(f"File not found: {media}")

    sample_rate = 16000
    try:
        with av.open(str(media)) as container:
            if not container.streams.audio:
                parser.exit(1, "The file has no audio track.\n")
        audio = decode_audio(str(media), sampling_rate=sample_rate)
    except (av.error.FFmpegError, OSError, ValueError) as error:
        parser.exit(1, f"Could not read the audio track: {error}\n")

    start = round(args.start * sample_rate)
    end = round(args.end * sample_rate) if args.end is not None else len(audio)
    samples = audio[start:end]
    if len(samples) < 0.1 * sample_rate:
        parser.exit(1, "The selected section contains less than 0.1 seconds of audio.\n")

    sound = parselmouth.Sound(samples, sampling_frequency=sample_rate)
    pitch = sound.to_pitch(time_step=0.01, pitch_floor=65, pitch_ceiling=650)
    frequencies = pitch.selected_array["frequency"]
    voiced = frequencies[frequencies > 0]
    print(f"Analyzed {args.start:.2f}–{(start + len(samples)) / sample_rate:.2f} s")
    coverage = voiced.size / frequencies.size if frequencies.size else 0
    print(f"Pitch detected in {coverage:.0%} of analyzed frames.")
    if voiced.size < 20:
        print("Voice impression: inconclusive (too little periodic audio).")
        return

    low, median, high = np.percentile(voiced, [10, 50, 90])
    print(f"Median pitch: {median:.0f} Hz")
    print(f"Middle 80% of pitch estimates: {low:.0f}–{high:.0f} Hz")
    if voiced.size < 100 or coverage < 0.35 or high / low >= 2:
        print("Voice impression: inconclusive (brief, sparse, or widely varying pitch).")


if __name__ == "__main__":
    main()
