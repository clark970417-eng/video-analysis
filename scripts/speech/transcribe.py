# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["faster-whisper==1.2.1", "av==16.1.0"]
# ///

import argparse
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher
from functools import cache
from itertools import groupby
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

from denoise import CACHE, denoise, denoise_samples, write_wav
from retry_windows import noisy_targets, shift_parts, trim_parts
from speaker_labels import speaker_label, speaker_turns


SAMPLE_RATE = 16000
SHORT_CLIP_SECONDS = 15
ENGLISH_DIGITS = dict(zip("zero one two three four five six seven eight nine".split(), "0123456789"))
DIGIT_WORD = re.compile(r"\b(?:" + "|".join(ENGLISH_DIGITS) + r")\b")


def normalized(words, language):
    words = unicodedata.normalize("NFKC", words).casefold()
    if language == "en":
        words = DIGIT_WORD.sub(lambda match: ENGLISH_DIGITS[match.group()], words)
    return "".join(
        char for char in words
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )


def overlapping_segments(first, second):
    """Group overlapping segments so a different split alone does not flag speech."""
    tagged = [(part.start, part.end, side, part)
              for side, parts in enumerate((first, second)) for part in parts]
    group = [[], []]
    start = end = 0.0
    for left, right, side, part in sorted(tagged, key=lambda item: item[:3]):
        if any(group) and left >= end - 0.08:
            yield start, end, group
            group = [[], []]
        if not any(group):
            start, end = left, right
        else:
            end = max(end, right)
        group[side].append(part)
    if any(group):
        yield start, end, group


def short_clip_review(first, second, language):
    for start, end, group in overlapping_segments(first, second):
        yield start, end, agreed_text(
            ([part.text.strip() for part in side] for side in group), language
        )


def comparison_text(parts):
    text = ""
    starts, ends, times = [], [], []
    for part in parts:
        if text:
            text += " "
        for word in part.words or ():
            starts.append(len(text))
            text += word.word
            ends.append(len(text))
            times.append((word.start, word.end))
        if not part.words:
            starts.append(len(text))
            text += part.text.strip()
            ends.append(len(text))
            times.append((part.start, part.end))
    return text, starts, ends, times


def changed_words(group, language):
    # CJK characters are compared individually because words may have no spaces.
    cjk = r"[\u3040-\u30ff\u3400-\u9fff]"
    word = rf"(?:(?!{cjk})[^\W_])+"
    pattern = rf"{cjk}|{word}(?:['’]{word})*"
    tracks = [comparison_text(side) for side in group]
    texts = [track[0] for track in tracks]
    tokens = [list(re.finditer(pattern, text)) for text in texts]
    keys = [[normalized(token.group(), language) for token in side] for side in tokens]

    def phrase(side, start, end):
        if start == end:
            return ""
        return texts[side][tokens[side][start].start():tokens[side][end - 1].end()]

    matcher = SequenceMatcher(None, *keys, autojunk=False)
    changes = []
    for tag, i, j, k, l in matcher.get_opcodes():
        if tag != "equal":
            spans = []
            for side, start, end in ((0, i, j), (1, k, l)):
                if start < end:
                    _, starts, ends, times = tracks[side]
                    left = bisect_right(ends, tokens[side][start].start())
                    right = bisect_left(starts, tokens[side][end - 1].end())
                    spans.extend(times[left:right])
            changes.append((
                phrase(0, i, j), phrase(1, k, l),
                min(a for a, _ in spans), max(b for _, b in spans),
            ))
    return changes


def agreed_text(group, language):
    original, retry = (" ".join(words) for words in group)
    return retry if normalized(original, language) == normalized(retry, language) else "[unclear]"


def label_lines(lines, candidates, turns, language):
    for start, end, text in lines:
        label = speaker_label(start, end, turns)
        if label in ("[multiple speaker turns]", "[overlapping speech]") and text != "[unclear]":
            words = [
                word for part in candidates if part.end > start and part.start < end
                for word in part.words or () if start <= (word.start + word.end) / 2 <= end
            ]
            if words and normalized("".join(word.word for word in words), language) == normalized(text, language):
                for name, group in groupby(words, key=lambda word: speaker_label(word.start, word.end, turns)):
                    items = list(group)
                    yield items[0].start, items[-1].end, name, "".join(word.word for word in items).strip()
                continue
        yield start, end, label, text


def low_confidence(segment):
    return segment.avg_logprob < -0.8 or segment.compression_ratio > 2.4


def retry_quality(lines, parts, model):
    """Prefer reviewed speech, with no preference for a wider window on ties."""
    clear_seconds = sum(end - start for start, end, text in lines if text != "[unclear]")
    unresolved = not lines or any(text == "[unclear]" for _, _, text in lines)
    if model == "small" and any(low_confidence(part) for part in parts):
        unresolved = True
        clear_seconds = sum(part.end - part.start for part in parts if not low_confidence(part))
    return not unresolved, clear_seconds, bool(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe local media with timestamped speech.")
    parser.add_argument("media", type=Path, help="Path to a local audio or video file")
    parser.add_argument("--language", metavar="CODE")
    parser.add_argument("--model", choices=("base", "small"), default="base")
    noise_options = parser.add_mutually_exclusive_group()
    noise_options.add_argument("--denoise", action="store_true", help="Force DeepFilterNet3 and compare transcripts")
    noise_options.add_argument("--no-denoise", action="store_true", help="Disable automatic noise reduction")
    parser.add_argument("--excerpts-dir", type=Path, help="Save WAV excerpts for unclear lines and wording changes")
    parser.add_argument("--speakers", action="store_true", help="Estimate speaker turns and overlapping speech")
    parser.add_argument("--speaker-count", type=int, metavar="N",
                        help="Use the known number of speakers and enable speaker labels")
    parser.add_argument("--segments-json", type=Path, help="Write structured speech segments to a new file")
    args = parser.parse_args()
    if args.speaker_count is not None and args.speaker_count < 1:
        parser.error("--speaker-count must be at least 1")
    speakers = args.speakers or args.speaker_count is not None

    media = args.media.expanduser().resolve()
    if not media.is_file():
        parser.error(f"File not found: {media}")

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    import av
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from faster_whisper.utils import format_timestamp
    from huggingface_hub.errors import LocalEntryNotFoundError

    @cache
    def load_model(size):
        options = {"device": "cpu", "compute_type": "int8", "download_root": str(CACHE)}
        try:
            return WhisperModel(size, local_files_only=True, **options)
        except LocalEntryNotFoundError:
            return WhisperModel(size, **options)

    def transcribe(model, audio, language, vad=True, timed=False):
        segments, info = model.transcribe(
            audio, language=language, vad_filter=vad, word_timestamps=timed
        )
        return [part for part in segments if part.text.strip()], info

    def review(audio, language, short_clip, timed=False, focus=None):
        first, info = transcribe(load_model(args.model), audio, language, timed=timed and (not short_clip or focus is not None))
        if focus is not None:
            first = trim_parts(first, *focus)
        detected_language = language or info.language
        if short_clip:
            second, _ = transcribe(load_model("small"), audio, language, timed=timed)
            if focus is not None:
                second = trim_parts(second, *focus)
            lines = list(short_clip_review(first, second, detected_language))
            return lines, second, detected_language, "small"

        lines = []
        for part in first:
            words = part.text.strip()
            if args.model == "base" and low_confidence(part):
                left = round(part.start * SAMPLE_RATE)
                right = round(part.end * SAMPLE_RATE)
                window = audio[left:right]
                retried = []
                if window.size:
                    retried, _ = transcribe(
                        load_model("small"), window, detected_language, vad=False
                    )
                words = agreed_text(
                    ([words], [segment.text.strip() for segment in retried]),
                    detected_language,
                )
            lines.append((part.start, part.end, words))
        return lines, first, detected_language, args.model

    try:
        with av.open(str(media)) as container:
            if not container.streams.audio:
                print("The file has no audio track.", file=sys.stderr)
                return 1

        original = decode_audio(str(media), sampling_rate=SAMPLE_RATE)
        if not original.size:
            print("No speech detected.")
            if args.segments_json:
                with args.segments_json.open("x", encoding="utf-8") as output:
                    json.dump([], output)
            return 0

        audio = original
        short_clip = args.model == "base" and len(original) <= SHORT_CLIP_SECONDS * SAMPLE_RATE
        lines, raw, language, comparison_model = review(
            original, args.language, short_clip, timed=True
        )
        candidates = raw
        comparisons = []
        filtered_ranges = []
        retry_language = args.language or (language if lines else None)
        if args.denoise:
            audio = denoise(media, decode_audio)
            filtered_lines, comparison, language, _ = review(
                audio, retry_language, short_clip, timed=True
            )
            comparisons.append((raw, comparison, language))
            filtered_ranges.append((0, len(audio)))
            if filtered_lines:
                lines, candidates = filtered_lines, comparison
        elif not args.no_denoise:
            # Explicit Small runs still show their candidate, but low confidence can trigger a retry.
            eligible = lines
            if args.model == "small":
                eligible = [
                    (start, end, "[unclear]" if low_confidence(part) else words)
                    for (start, end, words), part in zip(lines, raw)
                ]
            targets = list(noisy_targets(eligible, original, SAMPLE_RATE))
            if targets:
                try:
                    source_48k = decode_audio(str(media), sampling_rate=48000)
                    for left, right, replaced in targets:
                        start, end = left / SAMPLE_RATE, right / SAMPLE_RATE
                        best = None
                        previous_bounds = None
                        for context in (1, 4):
                            clip_start = max(0, left - context * SAMPLE_RATE)
                            clip_end = min(len(original), right + context * SAMPLE_RATE)
                            if (clip_start, clip_end) == previous_bounds:
                                break
                            previous_bounds = clip_start, clip_end
                            if context > 1:
                                print(
                                    f"Speech still unclear at {format_timestamp(start, always_include_hours=True)}. "
                                    "Trying up to four seconds of context on each side.",
                                    file=sys.stderr,
                                )
                            try:
                                cleaned = denoise_samples(
                                    source_48k[clip_start * 3:clip_end * 3], decode_audio
                                )
                                offset = clip_start / SAMPLE_RATE
                                filtered_lines, comparison, region_language, _ = review(
                                    cleaned, retry_language, short_clip, timed=True,
                                    focus=(start - offset, end - offset),
                                )
                            except (av.error.FFmpegError, OSError, RuntimeError) as error:
                                if best is None:
                                    raise
                                print(f"Wider retry unavailable: {error}. Kept the first retry.", file=sys.stderr)
                                break
                            quality = retry_quality(filtered_lines, comparison, args.model)
                            if best is None or quality > best[0]:
                                best = (
                                    quality, clip_start, cleaned, offset,
                                    filtered_lines, comparison, region_language,
                                )
                            if quality[0]:
                                break

                        _, clip_start, cleaned, offset, filtered_lines, comparison, region_language = best
                        comparisons.append((
                            trim_parts(raw, start, end),
                            shift_parts(comparison, offset), region_language,
                        ))
                        if filtered_lines:
                            lines = [line for line in lines if line[:2] != replaced]
                            lines.extend((a + offset, b + offset, words) for a, b, words in filtered_lines)
                            lines.sort()
                            if speakers:
                                if replaced:
                                    candidates = [
                                        part for part in candidates
                                        if not start <= (part.start + part.end) / 2 < end
                                    ]
                                candidates.extend(shift_parts(comparison, offset))
                                candidates.sort(key=lambda part: part.start)

                        if args.excerpts_dir:
                            if audio is original:
                                audio = original.copy()
                            audio[left:right] = cleaned[left - clip_start:right - clip_start]
                        filtered_ranges.append((left, right))
                        print(
                            f"Applied DeepFilterNet3 to unclear or undetected speech at "
                            f"{format_timestamp(start, always_include_hours=True)}–"
                            f"{format_timestamp(end, always_include_hours=True)}.",
                            file=sys.stderr,
                        )
                except (av.error.FFmpegError, OSError, RuntimeError) as error:
                    print(f"Automatic noise reduction stopped: {error}. Kept the remaining original audio.", file=sys.stderr)

        turns = speaker_turns(original, args.speaker_count) if speakers else []

        def save_excerpts(name, start, end):
            folder = args.excerpts_dir.expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            left = max(0, round((start - 0.4) * SAMPLE_RATE))
            right = round((end + 0.4) * SAMPLE_RATE)
            path = folder / f"{name}.wav"
            write_wav(path, original[left:right], SAMPLE_RATE)
            print(f"  Audio excerpt: {path}")
            if any(a < right and b > left for a, b in filtered_ranges):
                cleaned = folder / f"{name}-cleaned.wav"
                write_wav(cleaned, audio[left:right], SAMPLE_RATE)
                print(f"  Noise-reduced excerpt: {cleaned}")

        if not lines:
            print("No speech detected.")

        labeled = (
            label_lines(lines, candidates, turns, language)
            if speakers else ((start, end, "", words) for start, end, words in lines)
        )
        segments = []
        for number, (start, end, label, words) in enumerate(labeled, 1):
            begin_label = format_timestamp(start, always_include_hours=True)
            end_label = format_timestamp(end, always_include_hours=True)
            line = f"{label} {words}" if label else words
            print(f"[{begin_label} – {end_label}] {line}")
            if args.segments_json:
                segments.append(dict(start_s=start, end_s=end, text=words, speaker=label or None))
            if words == "[unclear]" and args.excerpts_dir:
                save_excerpts(f"unclear-{number:03d}", start, end)

        if speakers:
            print("\nEstimated speaker turns")
            for start, end, speaker in turns:
                print(f"[{format_timestamp(start, always_include_hours=True)} – "
                      f"{format_timestamp(end, always_include_hours=True)}] Speaker {speaker}")
            if not turns:
                print("No reliable speaker turns detected.")

        if comparisons:
            print(f"\nNoise reduction comparison (Whisper {comparison_model})")
            change_number = 0
            groups = (
                (start, end, group, region_language)
                for before, after, region_language in comparisons
                for start, end, group in overlapping_segments(before, after)
            )
            for start, end, group, region_language in groups:
                before, after = (" ".join(part.text.strip() for part in side) for side in group)
                changes = changed_words(group, region_language)
                if not changes:
                    continue
                change_number += 1
                print(f"[{format_timestamp(start, always_include_hours=True)} – "
                      f"{format_timestamp(end, always_include_hours=True)}] [changed by denoise]")
                print(f"  Original: {before or '(no speech detected)'}")
                print(f"  Noise-reduced: {after or '(no speech detected)'}")
                for index, (original_words, cleaned_words, left, right) in enumerate(changes, 1):
                    if right <= left:
                        left, right = start, end
                    print(f"  [{format_timestamp(left, always_include_hours=True)} – "
                          f"{format_timestamp(right, always_include_hours=True)}] Changed: "
                          f"{original_words or '(none)'} → {cleaned_words or '(none)'}")
                    if args.excerpts_dir:
                        save_excerpts(f"changed-{change_number:03d}-{index:02d}", left, right)
            if not change_number:
                print("No wording changes detected.")

        if args.segments_json:
            with args.segments_json.open("x", encoding="utf-8") as output:
                json.dump(segments, output, ensure_ascii=False)

    except (av.error.FFmpegError, OSError, RuntimeError, ValueError) as error:
        print(f"Transcription failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
