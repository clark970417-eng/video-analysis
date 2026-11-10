"""Find retry targets and keep their Whisper timings on the source timeline."""

from dataclasses import replace

from denoise import has_loud_steady_noise


def noisy_targets(lines, audio, rate):
    """Yield sample ranges for noisy gaps and unresolved lines."""
    cursor = 0
    targets = []
    for index, (start, end, words) in enumerate(lines):
        left = min(len(audio), round(start * rate))
        right = min(len(audio), round(end * rate))
        if left > cursor:
            targets.append((cursor, left, None))
        next_start = round(lines[index + 1][0] * rate) if index + 1 < len(lines) else len(audio)
        if words == "[unclear]" and cursor <= left < right <= next_start:
            targets.append((left, right, (start, end)))
        cursor = max(cursor, right)
    if cursor < len(audio):
        targets.append((cursor, len(audio), None))

    for left, right, replaced in targets:
        if has_loud_steady_noise(audio[left:right]):
            yield left, right, replaced
        elif replaced is None and right - left > 3 * rate:
            # A gap can include quiet margins on either side of a noisy passage.
            window = 3 * rate
            starts = range(left, right - window + 1, rate)
            pending = None
            for start in (*starts, right - window):
                end = start + window
                if not has_loud_steady_noise(audio[start:end]):
                    continue
                if pending and start > pending[1]:
                    yield pending[0], pending[1], None
                    pending = None
                pending = (pending[0] if pending else start, end)
            if pending:
                yield pending[0], pending[1], None


def trim_parts(parts, start, end):
    """Remove context words outside the target while keeping the original timing."""
    result = []
    for part in parts:
        if not part.words:
            if part.start >= start and part.end <= end:
                result.append(part)
            continue
        words = [
            replace(word, start=max(start, word.start), end=min(end, word.end))
            for word in part.words
            if start <= (word.start + word.end) / 2 < end
        ]
        if words:
            result.append(replace(
                part, start=words[0].start, end=words[-1].end,
                text="".join(word.word for word in words), words=words,
            ))
    return result


def shift_parts(parts, offset):
    return [
        replace(
            part, start=part.start + offset, end=part.end + offset,
            words=[
                replace(word, start=word.start + offset, end=word.end + offset)
                for word in part.words
            ] if part.words else part.words,
        )
        for part in parts
    ]
