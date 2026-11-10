# Video Analysis

## What is this?

Video Analysis is an Agent Skill for working with recordings. Give it a **video, an audio file, or a public media link**. It can transcribe what people say, measure what moves on screen, and put both on the original recording’s timeline.

Use it to summarize a podcast from its audio, jump to a phrase in an interview, or see which steps a presenter shows while explaining a tutorial. It runs in Codex, Claude Code, Google Antigravity, and HKUDS DeepCode. You can also run it from the terminal.

![Two timestamped frames show a red square changing position during a spoken line.](examples/timeline-preview.png)

In the [five-second sample](examples/speech-and-motion.mov), the spoken line starts at about **0:01**, and the square changes position at about **0:02**. The [example timeline](examples/timeline.json) records both events on the same clock.

## How does it work?

1. **Open the recording.** The skill reads a local file or downloads a supported public link. If you ask for a time range, it analyzes that section and keeps the original timestamps.
2. **Listen to the audio.** It transcribes speech directly from the recording, so YouTube captions are optional. It keeps the original language, marks unclear passages when detected, and can search for a spoken phrase or export subtitles.
3. **Inspect the video.** It measures motion and visual changes from the video frames. If you ask what is on screen, it exports timestamped stills so your agent can inspect objects, interface changes, or visible text.
4. **Line up speech and motion.** It accounts for different audio and video start times and maps speech across known audio gaps. When a cue spans a timestamp issue, the timeline can mark its timing as approximate.

### Try asking

- **Audio:** “Summarize this podcast from its audio and give me timestamps for the main points.”
- **Video:** “Watch this Illustrator tutorial through the end. Explain the finishing steps and show what changes on screen.”
- **Audio and video together:** “Find the first time they say ‘pricing’ and show me what’s on screen at that moment.”
- **Captions:** “Transcribe 12:30 to 14:00 in the original language and export SRT and WebVTT.”

## What does it support?

| Input | What you can get |
| --- | --- |
| Audio-only recordings, podcasts, and interviews | Timestamped speech, phrase matches, and SRT or WebVTT subtitles. |
| Silent video and screen recordings | Motion measurements and optional still frames for visual review. |
| Video with sound, including tutorials and presentations | A shared timeline of speech and motion, with optional frames around selected moments. |
| Public media links | Direct media links and supported video or audio pages. Supported YouTube and Vimeo links can include clickable jump times. |
| Local files and attachments | Audio or video files the agent can access on the machine where it runs. |

Public links must be downloadable. For sign-in or protected media, use an accessible local copy. A time range limits the analysis, though some sites still require downloading the full recording. Phrase search matches wording within individual speech segments. For a broader question, ask your agent to review the transcript and relevant frames together.

### Output files

| File | Contents |
| --- | --- |
| `timeline.json` | Aligned events, phrase matches, frame timestamps, timestamp issues, and jump links when available. |
| `transcript.txt` | Speech transcribed from the audio. |
| `motion.json` | Motion measurements for the full frame and its regions. |
| `subtitles.srt` and `subtitles.vtt` | Captions on the original source clock. |
| `frame-*.png` | Still frames exported on request. |

## How do I install it?

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git. Use a local agent session that can run commands and read the recording or generated files. The first run may download Python, dependencies, and speech model weights.

Clone the repository into your agent’s skill folder. Keep the whole directory so the agent can find the scripts next to `SKILL.md`. Run **one** of these commands:

```sh
# Codex
git clone https://github.com/clark970417-eng/video-analysis.git ~/.codex/skills/analyze-video

# Claude Code
git clone https://github.com/clark970417-eng/video-analysis.git ~/.claude/skills/analyze-video

# Google Antigravity App, CLI, or IDE
git clone https://github.com/clark970417-eng/video-analysis.git ~/.gemini/config/skills/analyze-video

# HKUDS DeepCode
git clone https://github.com/clark970417-eng/video-analysis.git ~/.agents/skills/analyze-video
```

| Agent | How to start |
| --- | --- |
| Codex | Mention `$analyze-video` in your prompt. |
| Claude Code | Type `/analyze-video` and give it a link or file. |
| Google Antigravity | Type `/analyze-video` in the App or CLI, or ask for the skill by name in the IDE. |
| HKUDS DeepCode | Mention `$analyze-video` or select it with `/skill analyze-video`. |

For a project-only install in Antigravity or DeepCode, clone into `.agents/skills/analyze-video` from your project root. Open a new agent session if the skill does not appear. See the official [Claude Code](https://code.claude.com/docs/en/skills), [Antigravity](https://antigravity.google/docs/skills), and [DeepCode](https://github.com/HKUDS/DeepCode/blob/main/docs/guide/skills-and-memory.md) guides.

### Run without an agent

From the installed skill directory, try the sample:

```sh
uv run --script scripts/analyze_media.py --output ./sample-analysis \
  --language en --find "red square" --frame-at 00:01.2 --frame-at 00:02.2 \
  -- examples/speech-and-motion.mov
```

Use a new output directory for each run. Replace the sample path with a quoted media link or local file path. Use `--start 12:30 --end 14:00` for a section, `--visual` for representative frames, or `--help` for the full option list.

To test the bundled workflow, run `uv run --script tests/smoke.py`. The test covers transcription, motion, time ranges, search, frame export, subtitles, jump-link formatting, and audio-gap alignment. The sample voice was generated with [eSpeak NG](https://github.com/espeak-ng/espeak-ng).
