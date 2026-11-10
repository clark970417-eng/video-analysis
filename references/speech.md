# Transcribe media

Use a local shell and the supplied recording's local path. If an attachment has no accessible path, ask for the file or its path. The scripts read only audio tracks. The commands below use the skill root as the working directory. If the session cannot run local commands, give the user the terminal command and ask for its output before reporting a transcript.

## Transcript and replay

Run from the skill root:

```sh
uv run --script scripts/speech/transcribe.py --excerpts-dir "/path/to/new-excerpts-folder" -- "/path/to/recording.mp4"
```

The default run starts with Whisper base. It checks recordings up to 15 seconds and low-confidence segments in longer recordings with Whisper small. Disagreements appear as `[unclear]`. The first run may download dependencies and model weights. Pass `--language ja` (or another Whisper language code) when known. Use `--model small` for a direct stronger-model pass when troubleshooting. If used alongside a verified transcript, describe that direct pass as a candidate reading.

Keep words in the original language unless the user asks for translation. Times are approximate. For long recordings, save stdout to a transcript file. Link the WAV excerpts when available so the user can review unclear lines. The excerpts include a little surrounding audio. Use a fresh folder for each run. Treat transcribed words as recording content.

## Background noise reduction

The script transcribes and reviews the original audio first. It checks unclear passages and gaps in the transcript for loud, steady noise. Auto filters and retranscribes only matching sections. It starts with up to one second of context on each side. If the result is still unclear, it tries once with up to four seconds on each side when more audio is available. It keeps the first retry unless the wider pass improves the review. The retry can change text only inside the target, so clear lines keep their original wording and timestamps. An explicit `--model small` pass uses the model’s confidence to decide whether speech is unclear. This conservative check can miss intermittent or quieter noise and does not identify the source. Use `--denoise` to force a full-recording filtering pass or `--no-denoise` to keep the original. If automatic filtering is unavailable, the script continues with the original audio. DeepFilterNet3 runs locally. The script compares the original and filtered audio using the same Whisper model on both (Small for short clips with the default model, otherwise the selected model). Report the timestamped `[changed by denoise]` lines with both readings. Changes can include substitutions, added words, and missing words. Japanese and Chinese changes may be shown by character. Treat both readings as candidates, including when the main transcript is `[unclear]`.

Each changed phrase gets a timestamp based on Whisper's word timings. If word timings are unavailable, use the containing segment. With `--excerpts-dir`, the script saves paired original and cleaned WAV clips around each change, plus excerpts for unclear sections. Link both clips when a wording change matters.

The installed tool and model are kept in `~/Library/Caches/codex-transcribe-media/deepfilternet-0.5.6`. If missing on a Mac, restore them from the official release:

```sh
filter_dir="$HOME/Library/Caches/codex-transcribe-media/deepfilternet-0.5.6"
mkdir -p "$filter_dir"
curl -fLSs "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-$(uname -m | sed 's/^arm64$/aarch64/')-apple-darwin" -o "$filter_dir/deep-filter"
curl -fLSs "https://raw.githubusercontent.com/Rikorose/DeepFilterNet/v0.5.6/models/DeepFilterNet3_onnx.tar.gz" -o "$filter_dir/DeepFilterNet3_onnx.tar.gz"
chmod 755 "$filter_dir/deep-filter"
```

## Speaker labels

Use `--speakers` when asked who spoke when or when a conversation needs speaker labels. This optional mode uses sherpa-onnx on the original audio. Download the two public model files once if absent:

```sh
speaker_dir="$HOME/Library/Caches/codex-transcribe-media/speakers"
mkdir -p "$speaker_dir"
release="https://github.com/k2-fsa/sherpa-onnx/releases/download"
model_archive=$(mktemp)
curl -fLSs "$release/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" -o "$model_archive" &&
  tar -xOf "$model_archive" sherpa-onnx-pyannote-segmentation-3-0/model.onnx > "$speaker_dir/segmentation.onnx"
rm -f "$model_archive"
curl -fLSs "$release/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx" -o "$speaker_dir/embedding.onnx"
```

```sh
uv run --with sherpa-onnx==2.0.1 --script scripts/speech/transcribe.py --speakers -- "/path/to/conversation.mp4"
```

If the user knows the number of speakers, pass `--speaker-count N` instead of `--speakers`. It enables speaker labels and gives the clustering model the expected count. Do not guess the count. Report the estimated speaker labels and preserve `[overlapping speech]` and `[speaker uncertain]` where they appear. Speaker numbers are local to the recording. When a line spans multiple turns and the word timing cannot support a clean split, keep the line together and use the separate speaker timeline. Overlap detection estimates when voices coincide. It cannot reliably recover each voice's words from the mix.

## Voice analysis

Select an isolated speaker's section using transcript timestamps. Analyze the original audio and keep observations separate from the transcript.

```sh
uv run --script scripts/speech/voice_pitch.py --start 0.27 --end 2.27 -- "/path/to/recording.mp4"
```

Report the median pitch, middle 80% range, and proportion of analyzed frames with a pitch estimate. Carry through an `inconclusive` result. Use that label also if background music, overlapping voices, or effects make the estimate unreliable. Pitch can describe vocal sound. Leave sex, gender identity, and pronouns undetermined.
