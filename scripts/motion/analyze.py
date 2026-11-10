# /// script
# requires-python = ">=3.10"
# dependencies = ["av", "numpy", "opencv-python-headless"]
# ///
"""Measure changes and tracked feature displacement in a local video."""
import argparse
import csv
import json
import math
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import av
import cv2
import numpy as np

from css_export import export_css
from element import ElementTracker


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="Local file or public HTTP(S) video URL")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=math.inf)
    parser.add_argument("--roi", type=float, nargs=4, action="append",
                        help="Repeat to compare regions. Default: full frame and four quadrants.")
    parser.add_argument("--element", type=float, nargs=4, action="append",
                        help="Repeat to track elements from normalized x y width height boxes at --start")
    parser.add_argument("--recovery-window", type=float, default=0.5,
                        help="Seconds to look for a lost element (0 disables recovery)")
    parser.add_argument("--css", type=Path, help="Write sampled element motion to a new CSS file")
    parser.add_argument("--settle-tolerance", type=float, default=2.0,
                        help="Settling radius in source pixels (default: 2)")
    parser.add_argument("--settle-hold", type=float, default=0.2,
                        help="Minimum time inside the settling radius in seconds (default: 0.2)")
    parser.add_argument("--pixel-threshold", type=int, default=12)
    parser.add_argument("--motion-fraction", type=float, default=0.003)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    boxes = (args.roi or []) + (args.element or [])
    for x, y, width, height in boxes:
        if not (0 <= x < x + width <= 1 and 0 <= y < y + height <= 1):
            parser.error("ROI must fit within the image and have positive area")
    if not (0 <= args.start < args.end and math.isfinite(args.start)):
        parser.error("Expected 0 <= start < end")
    if not (1 <= args.pixel_threshold <= 255 and 0 < args.motion_fraction <= 1):
        parser.error("Invalid motion thresholds")
    if not all(math.isfinite(value) and value > 0
               for value in (args.settle_tolerance, args.settle_hold)):
        parser.error("Settling tolerance and hold time must be finite and positive")
    if not math.isfinite(args.recovery_window) or args.recovery_window < 0:
        parser.error("Recovery window must be finite and nonnegative")
    if args.css and not args.element:
        parser.error("CSS export requires at least one --element")
    if args.element and args.csv and not args.roi:
        parser.error("CSV contains regional measurements. Add --roi for CSV alongside the element path in JSON")
    return args


def grayscale(frame):
    # Resize the decoded luma before tracking. Keep the scale for source-pixel units.
    scale = min(1, 720 / max(frame.width, frame.height))
    columns = max(1, round(frame.width * scale))
    rows = max(1, round(frame.height * scale))
    gray = frame.reformat(width=columns, height=rows, format="gray").to_ndarray()
    return gray, np.array([frame.width / columns, frame.height / rows])


def region(gray, roi):
    x, y, width, height = roi
    rows, columns = gray.shape
    left, top = int(x * columns), int(y * rows)
    right, bottom = round((x + width) * columns), round((y + height) * rows)
    crop = gray[top:bottom, left:right]
    if min(crop.shape) < 8:
        raise ValueError("ROI is too small for motion analysis")
    return crop


def displacement(previous, current, scale):
    points = cv2.goodFeaturesToTrack(
        previous, maxCorners=300, qualityLevel=0.01, minDistance=5
    )
    if points is None:
        return None, None, 0
    moved, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None)
    if moved is None:
        return None, None, 0
    returned, reverse_status, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None)
    if returned is None:
        return None, None, 0
    error = np.linalg.norm((returned - points).reshape(-1, 2), axis=1)
    delta = (moved - points).reshape(-1, 2)
    reliable = (status.ravel() == 1) & (reverse_status.ravel() == 1) & (error < 1)
    moving = reliable & (np.linalg.norm(delta, axis=1) >= 0.5)
    count = int(moving.sum())
    if count < 3:
        return None, None, count
    dx, dy = np.median(delta[moving], axis=0) * scale
    return float(dx), float(dy), count


def finish_interval(event):
    duration = event["end_s"] - event["start_s"]
    event["duration_s"] = duration
    samples = event.pop("samples")
    tracked = sum(dt for _, dt, _, _ in samples)
    event["tracked_fraction"] = min(1.0, tracked / duration)
    event["mean_dx_px_s"] = sum(dx for _, _, dx, _ in samples) / tracked if tracked else None
    event["mean_dy_px_s"] = sum(dy for _, _, _, dy in samples) / tracked if tracked else None

    # Summarize speed in time bins. Report coverage so absent tracks are not treated as zero.
    bins = []
    bin_count = min(10, max(1, math.ceil(duration / 0.1)))
    for i in range(bin_count):
        start = event["start_s"] + duration * i / bin_count
        end = event["start_s"] + duration * (i + 1) / bin_count
        weighted_speed = coverage = 0.0
        for time, dt, dx, dy in samples:
            overlap = max(0, min(time + dt, end) - max(time, start))
            weighted_speed += overlap * math.hypot(dx, dy) / dt
            coverage += overlap
        bins.append(dict(start_s=start, end_s=end, tracked_fraction=min(1.0, coverage / (end - start)),
                         speed_px_s=weighted_speed / coverage if coverage else None))
    event["speed_profile"] = bins
    reliable_bins = [item for item in bins if item["tracked_fraction"] >= 0.5]
    event["peak_speed_bin"] = max(reliable_bins, key=lambda item: item["speed_px_s"], default=None)
    return event


class MotionTrack:
    def __init__(self, name, roi):
        self.name = name
        self.roi = roi
        self.event = None
        self.intervals = []
        self.candidates = []

    def finish(self):
        if self.event:
            self.intervals.append(finish_interval(self.event))
            self.event = None

    def measure(self, previous, current, start, end, scale, args, writer):
        previous, current = region(previous, self.roi), region(current, self.roi)
        fraction = float(np.mean(cv2.absdiff(previous, current) >= args.pixel_threshold))
        candidate = fraction >= 0.65
        active = fraction >= args.motion_fraction and not candidate
        dx, dy, tracks = displacement(previous, current, scale) if active else (None, None, 0)
        if writer:
            writer.writerow((self.name, start, end, fraction, dx, dy, tracks, candidate))
        if candidate:
            self.candidates.append(dict(start_s=start, end_s=end, changed_fraction=fraction))
        if not active:
            self.finish()
            return
        if self.event is None:
            self.event = dict(start_s=start, samples=[], peak_changed_fraction=0.0)
        self.event["end_s"] = end
        self.event["peak_changed_fraction"] = max(self.event["peak_changed_fraction"], fraction)
        if dx is not None:
            self.event["samples"].append((start, end - start, dx, dy))

    def report(self):
        self.finish()
        return dict(roi=self.roi, motion_intervals=self.intervals, large_change_candidates=self.candidates)


def motion_tracks(rois):
    if rois:
        return [MotionTrack(f"region_{index}", roi) for index, roi in enumerate(rois, 1)]
    return [
        MotionTrack("full_frame", (0, 0, 1, 1)),
        MotionTrack("top_left", (0, 0, 0.5, 0.5)),
        MotionTrack("top_right", (0.5, 0, 0.5, 0.5)),
        MotionTrack("bottom_left", (0, 0.5, 0.5, 0.5)),
        MotionTrack("bottom_right", (0.5, 0.5, 0.5, 0.5)),
    ]


def analyze(args, video, writer):
    if not video.streams.video:
        raise ValueError("File has no video stream")
    stream = video.streams.video[0]
    origin = float(stream.start_time * stream.time_base) if stream.start_time is not None else None
    tracks = motion_tracks(args.roi) if args.roi or not args.element else []
    elements = [ElementTracker(box, args.recovery_window) for box in args.element or []]
    previous = previous_time = first = last = None
    steps = []
    timestamp_issues = []
    rate = float(stream.average_rate) if stream.average_rate else 0
    gap_limit = max(0.25, 5 / rate) if rate else 0.25
    if origin is not None and args.start > 0:
        try:
            video.seek(int((origin + args.start) / stream.time_base), stream=stream)
        except av.error.FFmpegError:
            pass
    missing = nonincreasing = count = 0
    for frame in video.decode(stream):
        if frame.time is None:
            missing += 1
            if previous_time is not None:
                timestamp_issues.append(dict(kind="missing_timestamp", start_s=previous_time, end_s=previous_time))
            previous = previous_time = None
            for track in tracks:
                track.finish()
            continue
        if origin is None:
            origin = frame.time
        time = frame.time - origin
        if time < args.start:
            continue
        if time > args.end:
            break
        if first is None:
            first = time
        last = time
        count += 1
        if previous_time is not None:
            delta = time - previous_time
            if delta <= 0 or delta > gap_limit:
                timestamp_issues.append(dict(
                    kind="regression" if delta <= 0 else "possible_gap",
                    start_s=min(previous_time, time), end_s=max(previous_time, time),
                ))
        current, scale = grayscale(frame)
        for element in elements:
            comparable = previous is not None and time > previous_time
            element.measure(previous if comparable else None, current, time, scale)
        if previous is not None:
            dt = time - previous_time
            if dt <= 0:
                nonincreasing += 1
            if dt > 0 and current.shape == previous.shape:
                steps.append(dt)
                for track in tracks:
                    track.measure(previous, current, previous_time, time, scale, args, writer)
            else:
                for track in tracks:
                    track.finish()
        previous, previous_time = current, time
    if not steps:
        raise ValueError("Selected range contains no comparable, timestamped frame pairs")
    element_reports = {
        f"element_{index}": element.report(args.settle_tolerance, args.settle_hold)
        for index, element in enumerate(elements, 1)
    }
    return {
        "width_px": stream.width,
        "height_px": stream.height,
        "first_frame_s": first,
        "last_frame_s": last,
        "frames_analyzed": count,
        "missing_timestamps": missing,
        "timestamp_issues": timestamp_issues,
        "nonincreasing_timestamps": nonincreasing,
        "frame_spacing_s": {"min": min(steps), "median": float(np.median(steps)), "max": max(steps)},
        "tracks": {track.name: track.report() for track in tracks},
        **({"element": element_reports["element_1"]} if len(elements) == 1 else {}),
        **({"elements": element_reports} if len(elements) > 1 else {}),
    }


def resolve_video(source, stack):
    path = Path(source).expanduser()
    if path.is_file():
        return path.resolve(), {"kind": "file"}
    url = urlsplit(source)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("Expected an existing local file or a public HTTP(S) video URL")
    downloader = [shutil.which("yt-dlp")] if shutil.which("yt-dlp") else ["uvx", "yt-dlp"]
    directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="video-motion-")))
    result = subprocess.run(
        downloader + [
            "--ignore-config", "--no-playlist", "--playlist-items", "1", "--no-progress",
            "--socket-timeout", "20", "-f", "bestvideo/best",
            "-o", str(directory / "video.%(ext)s"),
            "--print", "after_move:%()j", "--", source,
        ],
        capture_output=True, text=True,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.strip().splitlines()
        raise ValueError(detail[-1] if detail else "Could not download a video from this link")
    item = json.loads(result.stdout.strip().splitlines()[-1])
    path = Path(item["filepath"]).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise ValueError("The downloader did not return a local video file")
    return path, dict(kind="url", host=url.hostname, extractor=item.get("extractor_key"),
                      format_id=item.get("format_id"), container=item.get("ext"))


def main():
    args = options()
    try:
        with ExitStack() as stack:
            path, source = resolve_video(args.video, stack)
            video = stack.enter_context(av.open(str(path)))
            writer = None
            if args.csv:
                output = stack.enter_context(args.csv.open("x", newline=""))
                writer = csv.writer(output)
                writer.writerow(("track", "start_s", "end_s", "changed_fraction", "dx_px", "dy_px",
                                 "moving_tracks", "large_change_candidate"))
            result = analyze(args, video, writer)
            result["source"] = source
            if args.css:
                elements = result.get("elements") or {"element_1": result["element"]}
                css, summaries = export_css(elements)
                if not summaries:
                    raise ValueError("CSS export requires at least two consecutive tracked frames")
                with args.css.open("x", encoding="utf-8") as output:
                    output.write(css)
                result["css_export"] = {"path": str(args.css.resolve()), "animations": summaries}
    except (OSError, ValueError, cv2.error) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
