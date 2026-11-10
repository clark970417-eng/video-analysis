# Video Analysis

A Codex skill that transcribes speech, measures video motion, and aligns both on one timeline. It accepts public media links and local audio or video files. Search the transcript for a phrase, inspect frames around a match, or export subtitles.

## Install and try it

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). Then clone the repository into your Codex skills directory:

```sh
git clone https://github.com/clark970417-eng/video-analysis.git ~/.codex/skills/analyze-video
```

In a Codex chat, say “Use `$analyze-video` on this link from 12:30 to 14:00” and paste a link. You can also attach a media file.

From the skill folder, try the included five-second clip:

```sh
uv run --script scripts/analyze_media.py --output ./sample-analysis \
  --find "red square" --visual -- examples/speech-and-motion.mov
```

The first run downloads dependencies and speech model weights. Public media links may need a full download even when you select a time range. Sign-in and protected media require an accessible copy of the source.

## Reports

- `timeline.json` uses the original source clock, maps speech across known audio gaps, and includes phrase matches and supported YouTube or Vimeo jump links.
- `transcript.txt` and `motion.json` contain detailed speech and motion results.
- `subtitles.srt` and `subtitles.vtt` contain source-timed captions.
- `--visual` exports representative frames or frames near a phrase match. Use `--frame-at` for specific timestamps.

In the [sample clip](examples/speech-and-motion.mov), speech starts around 1 second and the red square changes position around 2 seconds. See the [example timeline](examples/timeline.json). The sample uses a synthetic voice generated with [eSpeak NG](https://github.com/espeak-ng/espeak-ng).

## Run the smoke test

```sh
uv run --script tests/smoke.py
```

The test checks speech, motion, the one-second audio offset, bounded analysis, frame export, phrase search, subtitle output, jump links, and timestamp mapping after gaps.
