# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["imageio-ffmpeg"]
# ///
"""Run the packaged analyzer on the sample clip and a version with timestamp gaps."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

import imageio_ffmpeg


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples/speech-and-motion.mov"
sys.path.insert(0, str(ROOT / "scripts"))
from timeline_output import jump_url, map_speech, search_speech


def analyze(source, output, *options):
    subprocess.run([
        "uv", "run", "--script", str(ROOT / "scripts/analyze_media.py"),
        "--output", str(output), "--language", "en", *options, "--", str(source),
    ], check=True)
    return json.loads((output / "timeline.json").read_text(encoding="utf-8"))


def main():
    with tempfile.TemporaryDirectory(prefix="video-analysis-test-") as directory:
        scratch = Path(directory)
        report = analyze(SAMPLE, scratch / "full", "--find", "RED square", "--visual")
        assert not report["notes"], report["notes"]
        assert abs(report["track_offsets_s"]["audio"] - 1.0) < 0.04
        speech = next(event for event in report["timeline"] if event["kind"] == "speech")
        assert "square" in speech["text"].lower() and abs(speech["start_s"] - 1.0) < 0.4, speech
        assert any(event["kind"] == "motion" and event["track"] == "full_frame"
                   and abs(event["end_s"] - 2.0) < 0.12 for event in report["timeline"])
        assert report["search"]["matches"][0] == speech
        assert abs(report["frames"][0]["time_s"] - speech["start_s"]) <= 0.04
        assert not search_speech(report["timeline"], "missing phrase", 8)
        assert "00:00:01," in (scratch / "full/subtitles.srt").read_text()
        vtt = (scratch / "full/subtitles.vtt").read_text()
        assert vtt.startswith("WEBVTT\n\n") and "00:00:01." in vtt and "square" in vtt
        assert jump_url("https://www.youtube.com/watch?v=example&t=2", 90.9) == (
            "https://www.youtube.com/watch?v=example&t=90s"
        )
        assert jump_url("https://vimeo.com/123", 90) == "https://vimeo.com/123#t=90s"
        assert jump_url("https://example.com/clip.mp4", 90) is None

        clip = analyze(SAMPLE, scratch / "clip", "--start", "00:01.2", "--end", "00:02.8")
        assert not clip["notes"], clip["notes"]
        assert abs(clip["transcript_offset_s"] - 1.2) < 0.001
        assert {"speech", "motion"} <= {event["kind"] for event in clip["timeline"]}
        assert all(1.2 <= event["start_s"] <= event["end_s"] <= 2.8 for event in clip["timeline"])

        damaged = scratch / "gap.mkv"
        subprocess.run([
            imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error",
            "-copyts", "-i", str(SAMPLE), "-vf", "select=not(between(t\\,2.5\\,3.5))",
            "-af", "aselect=not(between(t\\,1.5\\,1.8))",
            "-fps_mode", "vfr", "-c:v", "libx264", "-c:a", "pcm_s16le", str(damaged),
        ], check=True)
        gap = analyze(damaged, scratch / "gap-report")
        assert {("video", "possible_gap"), ("audio", "gap")} <= {
            (issue["track"], issue["kind"]) for issue in gap["sync_issues"]
        }, gap["sync_issues"]
        anchors = gap["audio_time_map"]
        assert len(anchors) >= 2, anchors
        boundary = anchors[1]["transcript_s"]
        cue = dict(start_s=boundary + 0.05, end_s=boundary + 0.1, text="after the gap", speaker=None)
        mapped = map_speech(cue, anchors, 0, float("inf"))
        assert abs(mapped["start_s"] - anchors[1]["source_s"] - 0.05) < 0.001
        assert mapped["start_s"] > cue["start_s"] + anchors[0]["source_s"] + 0.2
        assert not mapped["approximate_sync"]
        assert any(event["kind"] == "speech" and event["approximate_sync"] for event in gap["timeline"])
    print("Speech, motion, search, frame export, subtitles, jump links, source ranges, and gap mapping passed.")


if __name__ == "__main__":
    main()
