"""Map transcript times and format searchable timeline outputs."""

from bisect import bisect_left, bisect_right
from html import escape
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def map_speech(segment, anchors, fallback, uncertain_after):
    """A boundary at a gap ends on its left and starts on its right."""
    start, end = segment["start_s"], segment["end_s"]
    times = [anchor["transcript_s"] for anchor in anchors]
    left = max(0, bisect_right(times, start + 1e-8) - 1)
    right = max(left, bisect_left(times, end - 1e-8) - 1)

    def source_time(time, index):
        if not anchors:
            return time + fallback
        anchor = anchors[index]
        return time - anchor["transcript_s"] + anchor["source_s"]

    mapped_start = source_time(start, left)
    mapped_end = source_time(end, right)
    return {
        "kind": "speech",
        "start_s": round(max(0, mapped_start), 6),
        "end_s": round(max(0, mapped_start, mapped_end), 6),
        "text": segment["text"],
        "speaker": segment["speaker"],
        "approximate_sync": not anchors or left != right or end >= uncertain_after,
    }


def jump_url(source, seconds):
    url = urlsplit(source)
    if url.scheme not in {"http", "https"} or url.username or url.password:
        return None
    query = parse_qsl(url.query, keep_blank_values=True)
    timestamp = max(0, int(seconds))
    host = url.hostname
    youtube_page = host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and (
        (url.path == "/watch" and any(key == "v" and value for key, value in query))
        or url.path.startswith(("/shorts/", "/live/"))
    )
    youtube_short_link = host == "youtu.be" and bool(re.fullmatch(r"/[^/]+/?", url.path))
    if youtube_page or youtube_short_link:
        query = [(key, value) for key, value in query if key not in {"t", "start"}]
        return urlunsplit(url._replace(query=urlencode([*query, ("t", f"{timestamp}s")]), fragment=""))
    if host in {"vimeo.com", "www.vimeo.com"} and url.path.strip("/").isdecimal():
        return urlunsplit(url._replace(fragment=f"t={timestamp}s"))
    return None


def search_speech(timeline, phrase, limit):
    # Word boundaries prevent "price" from matching "prices". Punctuation and
    # spacing can vary between the query and the transcript.
    words = re.findall(r"\w+", phrase.casefold())
    if not words:
        raise ValueError("Search requires at least one letter or number.")
    query = " ".join(words)
    matches = []
    for event in timeline:
        if event["kind"] != "speech":
            continue
        normalized = " ".join(re.findall(r"\w+", event["text"].casefold()))
        if f" {query} " in f" {normalized} " or (
            any(ord(char) > 127 for char in query) and query in normalized
        ):
            matches.append(event)
            if len(matches) == limit:
                break
    return matches


def subtitle_time(seconds, separator):
    total = max(0, round(seconds * 1000))
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole:02}{separator}{milliseconds:03}"


def write_subtitles(timeline, output):
    cues = [event for event in timeline if event["kind"] == "speech"
            and round(event["end_s"] * 1000) > round(event["start_s"] * 1000)]
    for extension, separator, header in (("srt", ",", ""), ("vtt", ".", "WEBVTT\n\n")):
        with (output / f"subtitles.{extension}").open("w", encoding="utf-8") as file:
            file.write(header)
            for index, event in enumerate(cues, 1):
                start = subtitle_time(event["start_s"], separator)
                end = subtitle_time(event["end_s"], separator)
                text = " ".join(event["text"].split())
                if event["speaker"]:
                    text = f'{event["speaker"]} {text}'
                file.write(f"{index}\n{start} --> {end}\n{escape(text, quote=False)}\n\n")
