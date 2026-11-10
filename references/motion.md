# Analyze video motion

Pass the original local file, accessible attachment path, or public video URL to the analyzer.

```bash
uv run <skill-dir>/scripts/motion/analyze.py "VIDEO_FILE_OR_URL"
```

Quote the input. The analyzer accepts direct video links and uses yt-dlp for supported public pages and streams, including X. It downloads one video rendition to a temporary directory, analyzes it locally, and cleans up the download. If a link requires login, has DRM, or cannot be downloaded, explain the access error and ask for the original file. The motion analyzer uses decoded frames in memory. For visual descriptions and on-screen text, use the optional frame review in the main skill.

The analyzer decodes frames in memory and uses presentation timestamps. It measures the full frame and four quadrants independently. Compare regions to distinguish simultaneous movements. Use repeatable `--roi 0.25 0.25 0.5 0.5` arguments to replace the defaults with tighter regions around the elements of interest. Coordinates are normalized x, y, width, height from the top left of the encoded frame. Check orientation metadata for rotated footage. Region tracks describe visible features within a fixed area. They can mix elements and do not maintain an object's identity when it leaves the area.

## Follow one element

Use `--element X Y WIDTH HEIGHT` to select a tight box around the element in the first frame at or after `--start`. Use the same normalized coordinates as for a region. Ask for the initial box or enough location and timing information to choose it. Select an element with distinct internal features and keep background outside the box. Give the clip enough time after the movement for a settled position to be visible.

Element mode returns one timestamped path, with center position and speed in source pixels, relative scale, and motion-aligned appearance measurements. Compare center movement, scale ratio, and mean luma or contrast ratio independently to describe slide, zoom, and fade-like changes. Contrast is a proxy for fading. Lighting, background changes, blur, or resampling can also change it. Scale assumes approximately uniform resizing. If tracking is lost, report the last reliable time and the loss. Do not interpolate through occlusions or substitute a nearby element.

Read the movement summary for onset, distance traveled, net displacement, first arrival, and settling. Travel distance suppresses small tracking jitter. The inferred destination comes from the last stable portion of the selected range. The default settling rule is to stay within 2 source pixels for at least 0.2 seconds. Adjust with `--settle-tolerance` and `--settle-hold`. Overshoot and destination crossings are measured along the start-to-destination axis. Report settling as unconfirmed when the tail is too short, still moving, or tracking was lost. For multiple consecutive animations, use separate time ranges.

Add explicit `--roi` options to run regional measurements alongside the element path. The element path is in JSON. CSV continues to contain regional frame-pair data.

## Measure regions

Use `--start 2.0 --end 4.5` for a segment and `--csv /absolute/path/to/new.csv` for per-frame measurements by region. The JSON reports each interval's onset, end, directional velocity, tracking coverage, speed profile, and peak speed bin. Read the speed profile to describe acceleration and settling. Treat missing or low-coverage bins as uncertain. A sustained decline in measured speed can support a description of easing. Avoid naming an exact easing curve from these measurements alone.

Report relevant times, directions, and uncertainty. Direction and speed are estimates in source pixels. A high change fraction may indicate a cut, flash, or large movement. Weak textures, fades, and small movements may show change without reliable direction. Narrow the region or adjust `--pixel-threshold` and `--motion-fraction` if needed. Report the source rendition for links and the frame interval when it limits timing precision. Tie semantic claims to the user's description of the region or subject. The analyzer measures visual motion and has no object labels or audio analysis.

## Recover and compare elements

Repeat `--element` to track several boxes independently from the same starting frame. Compare their timestamps on the shared video timeline. The report uses `element` for one box and `elements` for multiple boxes.

By default, the tracker searches for up to 0.5 seconds after losing a match. Set `--recovery-window SECONDS` to change this or use zero to disable recovery. Recovery requires a strong, distinct match near the last reliable location. It works best when the element keeps its appearance and has little rotation. Similar copies, long occlusions, and large jumps can prevent recovery. Read `gaps` and `segments` before reporting motion. Each resumed track starts a new measured segment, with no speed or distance inferred across the gap.

## Export measured motion as CSS

Use `--css /absolute/path/to/new.css` alongside one or more `--element` boxes. The export contains translation and uniform scale keyframes, with linear interpolation between measured samples. Apply the generated class to an element at its starting position and size. Delays use the original video timeline. For a track with gaps, each segment has a separate class and keyframes. Decide how the prototype should handle the missing span. The export reports rounding error at sampled frames. It does not estimate browser rendering error, motion between samples, or opacity from contrast.
