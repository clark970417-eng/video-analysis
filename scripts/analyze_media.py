# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["av==16.1.0", "pillow", "imageio-ffmpeg"]
# ///
"""Download or open media and report speech and motion on a shared timeline."""

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import av
import imageio_ffmpeg

from timeline_output import jump_url, map_speech, search_speech, write_subtitles


SCRIPTS = Path(__file__).resolve().parent


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Process exited with code {result.returncode}")
    return result


def resolve_media(source, scratch):
    path = Path(source).expanduser()
    if path.is_file():
        return path.resolve()
    url = urlsplit(source)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("Provide a local file or a public HTTP(S) media link.")
    result = run([
        "uvx", "yt-dlp", "--ignore-config", "--no-playlist", "--playlist-items", "1",
        "--socket-timeout", "20", "--retries", "1", "--no-progress",
        "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(),
        "-f", "bestvideo+bestaudio/best/bestaudio/bestvideo",
        "-o", str(scratch / "media.%(ext)s"), "--print", "after_move:filepath", "--", source,
    ])
    paths = result.stdout.strip().splitlines()
    if len(paths) != 1:
        raise ValueError("Expected one downloadable media item. Try the original media file.")
    path = Path(paths[0]).resolve()
    if path.parent != scratch.resolve() or not path.is_file():
        raise ValueError("The download did not produce a usable media file.")
    return path


def probe(path):
    tracks = {}
    for kind in ("audio", "video"):
        with av.open(str(path)) as container:
            streams = getattr(container.streams, kind)
            if not streams:
                continue
            stream = streams[0]
            first = next((frame.time for frame in container.decode(stream) if frame.time is not None), None)
            declared = float(stream.start_time * stream.time_base) if stream.start_time is not None else None
            # Speech is measured from decoded samples. Motion uses the declared stream start.
            start = declared if kind == "video" and declared is not None else first
            tracks[kind] = start
    return tracks


def parse_time(value):
    try:
        parts = value.split(":")
        if len(parts) > 3 or any(not part.isdecimal() for part in parts[:-1]):
            raise ValueError
        seconds = float(parts[-1])
        if not math.isfinite(seconds) or seconds < 0 or (len(parts) > 1 and seconds >= 60):
            raise ValueError
        if len(parts) == 3 and int(parts[1]) >= 60:
            raise ValueError
        return sum(int(part) * 60 ** index for index, part in enumerate(reversed(parts[:-1]), 1)) + seconds
    except (OverflowError, ValueError):
        raise argparse.ArgumentTypeError("Use seconds, MM:SS, or HH:MM:SS.") from None


def inspect_audio(path, start, end, origin, bounded):
    previous_end = None
    elapsed = 0.0
    uncertain_after = math.inf
    anchors, issues = [], []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            timestamp = frame.time
            rate = frame.sample_rate
            duration = frame.samples / rate
            if timestamp is None:
                if not bounded or anchors:
                    point = previous_end if previous_end is not None else origin
                    issues.append(dict(track="audio", kind="missing_timestamp",
                                       start_s=point - origin, end_s=point - origin))
                    uncertain_after = min(uncertain_after, elapsed)
                    elapsed += duration
                previous_end = None
                continue
            if timestamp >= end:
                break
            frame_end = timestamp + duration
            delta = timestamp - previous_end if previous_end is not None else 0
            if abs(delta) > 0.02 and max(timestamp, previous_end) >= start:
                issues.append(dict(
                    track="audio", kind="gap" if delta > 0 else "overlap",
                    start_s=max(start, min(timestamp, previous_end)) - origin,
                    end_s=max(timestamp, previous_end) - origin,
                ))
            if frame_end > start or not bounded:
                skipped = max(0, math.ceil((start - timestamp) * rate - 1e-6)) if bounded else 0
                stop = min(frame.samples, math.ceil((end - timestamp) * rate - 1e-6)) if math.isfinite(end) else frame.samples
                if stop > skipped:
                    if not anchors or previous_end is None or abs(delta) > 0.02:
                        anchors.append(dict(transcript_s=elapsed, source_s=timestamp + skipped / rate - origin))
                    elapsed += (stop - skipped) / rate
            previous_end = frame_end
    return anchors, issues, uncertain_after, elapsed


def clip_audio(path, output, start, end):
    filters = f"atrim=start={start:.9f}"
    if math.isfinite(end):
        filters += f":end={end:.9f}"
    run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error",
        "-copyts", "-i", str(path), "-map", "0:a:0", "-af", filters,
        "-c:a", "pcm_s16le", str(output),
    ])


def add_event(timeline, event, offset, kind, **details):
    timeline.append(dict(
        kind=kind, start_s=round(event["start_s"] + offset, 6),
        end_s=round(event["end_s"] + offset, 6), **details,
    ))


def sample_times(timeline, video_offset, motion):
    if not motion:
        return [video_offset]
    start = video_offset + motion["first_frame_s"]
    end = video_offset + motion["last_frame_s"]
    candidates = [
        item["end_s"] for item in timeline
        if item["kind"] in {"motion", "large_change"} and item["track"] == "full_frame"
    ]
    samples = [start, (start + end) / 2, end]
    for candidate in candidates:
        if len(samples) == 8:
            break
        if start <= candidate <= end and all(abs(candidate - time) >= 0.25 for time in samples):
            samples.append(candidate)
    return sorted(samples)


def export_frames(path, output, targets, origin, end):
    frames = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        pending = iter(sorted(set(targets)))
        target = next(pending, None)
        for frame in container.decode(stream):
            if target is None:
                break
            if frame.time is None:
                continue
            if frame.time > end:
                break
            if frame.time + 1e-9 < target:
                continue
            requested = []
            while target is not None and target <= frame.time + 1e-9:
                requested.append(target - origin)
                target = next(pending, None)
            image = frame.to_image()
            image.thumbnail((1280, 1280))
            filename = output / f"frame-{len(frames) + 1:02}.png"
            image.save(filename)
            frames.append(dict(time_s=round(frame.time - origin, 6),
                               requested_times_s=requested, path=str(filename)))
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Local media path or public media link")
    parser.add_argument("--output", type=Path, required=True, help="New directory for reports and excerpts")
    parser.add_argument("--start", type=parse_time, default=0.0, help="Start on the source timeline")
    parser.add_argument("--end", type=parse_time, help="End on the source timeline")
    parser.add_argument("--find", metavar="PHRASE", help="Find a phrase in the timestamped speech")
    parser.add_argument("--match-limit", type=int, default=8, help="Maximum number of phrase matches")
    parser.add_argument("--language", help="Known speech language code")
    parser.add_argument("--visual", action="store_true", help="Export representative video frames for visual review")
    parser.add_argument("--frame-at", type=parse_time, action="append", default=[],
                        help="Export the first frame at or after this timeline second. Repeat up to 8 times.")
    args = parser.parse_args()
    end = args.end if args.end is not None else math.inf
    if end <= args.start:
        parser.error("--end must be later than --start.")
    if len(args.frame_at) > 8 or any(not args.start <= t <= end for t in args.frame_at):
        parser.error("Provide up to 8 frame times within the selected range.")
    if args.match_limit < 1:
        parser.error("--match-limit must be positive.")
    if args.find is not None:
        try:
            search_speech([], args.find, args.match_limit)
        except ValueError as error:
            parser.error(str(error))
    bounded = args.start != 0 or args.end is not None
    output = args.output.expanduser().resolve()
    report = dict(timeline=[], frames=[], notes=[], track_offsets_s={}, sync_issues=[],
                  range_s=dict(start_s=args.start, end_s=args.end))
    with tempfile.TemporaryDirectory(prefix="analyze-media-") as temp:
        scratch = Path(temp)
        path = resolve_media(args.source, scratch)
        starts = probe(path)
        if not starts:
            raise ValueError("The file has no audio or video track.")
        output.mkdir(parents=True, exist_ok=False)
        origin = min((start for start in starts.values() if start is not None), default=0.0)
        for kind, start in starts.items():
            report["track_offsets_s"][kind] = None if start is None else start - origin
        if any(start is None for start in starts.values()):
            report["notes"].append("A track start could not be verified. Cross-track alignment is approximate.")
        if "audio" in starts:
            segments = scratch / "segments.json"
            command = [
                "uv", "run", "--script", str(SCRIPTS / "speech/transcribe.py"),
                "--segments-json", str(segments), "--excerpts-dir", str(output / "excerpts"),
            ]
            if args.language:
                command += ["--language", args.language]
            try:
                anchors, issues, uncertain_after, audio_duration = inspect_audio(
                    path, origin + args.start, origin + end, origin, bounded,
                )
                report["sync_issues"].extend(issues)
                audio_path = path
                if bounded:
                    if not anchors:
                        raise ValueError("No timestamped audio samples in the selected range.")
                    audio_path = scratch / "range.wav"
                    clip_audio(path, audio_path, origin + args.start, origin + end)
                offset = report["track_offsets_s"]["audio"] or 0
                report["transcript_offset_s"] = anchors[0]["source_s"] if anchors else offset
                report["audio_time_map"] = anchors
                transcript = run([*command, "--", str(audio_path)]).stdout
                (output / "transcript.txt").write_text(transcript, encoding="utf-8")
                for segment in json.loads(segments.read_text(encoding="utf-8")):
                    segment["end_s"] = min(segment["end_s"], audio_duration)
                    if segment["end_s"] > segment["start_s"]:
                        report["timeline"].append(map_speech(segment, anchors, offset, uncertain_after))
            except (av.error.FFmpegError, OSError, ValueError) as error:
                report["notes"].append(f"Speech pass failed: {error}")
        motion = None
        if "video" in starts:
            try:
                offset = report["track_offsets_s"]["video"] or 0
                command = ["uv", "run", "--script", str(SCRIPTS / "motion/analyze.py")]
                if bounded:
                    command += ["--start", str(max(0, args.start - offset))]
                    if args.end is not None:
                        if end < offset:
                            raise ValueError("Video starts after the selected range.")
                        command += ["--end", str(end - offset)]
                motion = json.loads(run([*command, "--", str(path)]).stdout)
                motion["source"] = {"kind": "file" if Path(args.source).expanduser().is_file() else "url"}
                (output / "motion.json").write_text(json.dumps(motion, indent=2), encoding="utf-8")
                video_issues = motion["timestamp_issues"]
                for issue in video_issues:
                    add_event(report["sync_issues"], issue, offset, issue["kind"], track="video")
                for track, measurements in motion["tracks"].items():
                    for key, kind in (("motion_intervals", "motion"), ("large_change_candidates", "large_change")):
                        for event in measurements[key]:
                            uncertain = starts["video"] is None or any(
                                issue["start_s"] <= event["end_s"] and issue["end_s"] >= event["start_s"]
                                for issue in video_issues
                            )
                            add_event(report["timeline"], event, offset, kind, track=track,
                                      approximate_sync=uncertain)
            except ValueError as error:
                report["notes"].append(f"Motion pass failed: {error}")
        report["timeline"].sort(key=lambda item: (item["start_s"], item["end_s"]))
        for event in report["timeline"]:
            url = jump_url(args.source, event["start_s"])
            if url:
                event["jump_url"] = url
        matches = []
        if args.find is not None:
            matches = search_speech(report["timeline"], args.find, args.match_limit)
            report["search"] = dict(query=args.find, matches=matches)
        if args.frame_at or args.visual:
            if "video" in starts:
                try:
                    match_times = [
                        time for event in matches[:4]
                        for time in (event["start_s"], (event["start_s"] + event["end_s"]) / 2)
                    ]
                    targets = args.frame_at or match_times or sample_times(
                        report["timeline"], report["track_offsets_s"]["video"] or 0, motion,
                    )
                    targets = [t for t in targets if args.start <= t <= end]
                    report["frames"] = export_frames(
                        path, output, [t + origin for t in targets], origin, end + origin,
                    )
                    exported = {round(t, 6) for frame in report["frames"] for t in frame["requested_times_s"]}
                    missing = [t for t in targets if round(t, 6) not in exported]
                    if missing:
                        report["notes"].append(f"No frame available at or after these times: {missing}")
                except (av.error.FFmpegError, OSError, ValueError) as error:
                    report["notes"].append(f"Frame export failed: {error}")
            else:
                report["notes"].append("Frame export requires a video track.")
    report["sync_issues"].sort(key=lambda item: item["start_s"])
    if report["sync_issues"]:
        report["notes"].append("Timestamp issues were detected. See sync_issues and approximate_sync on affected events.")
    if (output / "transcript.txt").exists():
        write_subtitles(report["timeline"], output)
    (output / "timeline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "timeline.json")
    return int(not any((output / name).exists() for name in ("transcript.txt", "motion.json"))
               and not report["frames"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, av.error.FFmpegError) as error:
        sys.exit(str(error))
