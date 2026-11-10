---
name: analyze-video
description: Analyze a pasted video or audio link or a local media file. Transcribe speech, measure visual motion, and align both on a timestamped timeline. Find where a phrase was said, export subtitles and jump links, or inspect frames to identify visible changes or read on-screen text.
---

# Video Analysis

Accept a public HTTP(S) link to a supported media page or a direct video or audio file, a local media path, or an accessible attachment. Analyze one media item per request. If a link requires sign-in, is protected, or cannot be downloaded, explain the access issue and ask for the original file.

The skill bundles both analyzers. It requires `uv`. The first run may download dependencies and speech model weights. The source is analyzed locally after any download.

## Run the combined analysis

From this skill's directory, use a fresh output directory:

```sh
uv run --script scripts/analyze_media.py --output "/absolute/path/to/new-analysis" -- "MEDIA_LINK_OR_PATH"
```

For a requested section, pass `--start` and/or `--end` as seconds, `MM:SS`, or `HH:MM:SS`. For example, use `--start 12:30 --end 14:00`. Timeline events and frame times stay on the original source clock. The download can still require the whole media item. Pass `--language` with the known speech language code if supplied. Quote the input. The runner downloads public links to a temporary directory, merges separate audio and video streams when needed, and cleans up the source download. It supports audio-only and silent video sources.

Read `timeline.json` for aligned speech and motion events, track offsets, and any failed passes. Times are seconds from the earliest known track start. Read `transcript.txt` for speaker or denoising detail and `motion.json` for measurements. The transcript uses decoded-audio time. For details outside the aligned timeline, use the last applicable `audio_time_map` anchor. Add the difference between the transcript time and the anchor's `transcript_s` to its `source_s`. The raw motion report uses video-track time. Use the combined timeline when comparing audio with video. Keep uncertain words and alternate readings, and link replay excerpts when they matter. Preserve the speech language unless asked to translate. Check `sync_issues` for missing timestamps, gaps, and overlaps. Events marked `approximate_sync` need qualified timing claims. A possible video gap can also reflect intentional variable frame spacing.

For specialized options on local media, read [speech](references/speech.md) or [motion](references/motion.md). These reference workflows include speaker labels, pitch, motion regions, object tracking, and CSS export.

## Find speech and export subtitles

Pass `--find "spoken phrase"` to look for words in the transcript. Use `--match-limit` to change how many chronological matches are returned. Search matches a phrase within one speech segment. For broader questions, read the aligned speech events and look for relevant wording. Return the time, quoted transcript, and `jump_url` when present. Combine `--find` with `--visual` to inspect frames at matching speech events. Use `--frame-at` for a closer look before or after a match.

Link `subtitles.srt` or `subtitles.vtt` when asked for captions. Both use the source clock. Speech after a known gap maps to the next audio timestamp. A cue spanning a gap can still have approximate timing.

## Optional visual description

If asked what appears, changes, or is written on screen, add `--visual` to export representative frames during the same run. When the relevant time is known, use repeatable `--frame-at SECONDS` options to choose up to eight specific times. For questions tied to a spoken line, inspect frames around its aligned timestamp. If you need a follow-up run, use a fresh output directory. The timeline records each PNG and its actual timestamp.

Open the exported frames using the available image viewer. Describe what is visible and quote only legible on-screen text. Compare frames before and after a suspected transition. Separate these observations from motion measurements and speech transcription. For claims about what happens between frames, use the measured motion and qualify what the samples cannot establish.
