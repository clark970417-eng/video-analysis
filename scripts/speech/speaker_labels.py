"""Optional local speaker diarization and transcript labels."""

from denoise import CACHE


def speaker_turns(audio, count=None):
    try:
        import sherpa_onnx as sherpa
    except ImportError as error:
        raise RuntimeError("Install speaker support with uv run --with sherpa-onnx==2.0.1.") from error

    folder = CACHE / "speakers"
    segmentation = folder / "segmentation.onnx"
    embedding = folder / "embedding.onnx"
    if not segmentation.is_file() or not embedding.is_file():
        raise RuntimeError(f"Speaker models are missing from {folder}. See the skill setup.")

    config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation))
        ),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=str(embedding)),
        clustering=sherpa.FastClusteringConfig(num_clusters=count if count is not None else -1, threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.3,
    )
    if not config.validate():
        raise RuntimeError("The speaker diarization configuration is invalid.")
    diarizer = sherpa.OfflineSpeakerDiarization(config)
    names = {}
    return [
        (part.start, part.end, names.setdefault(part.speaker, len(names) + 1))
        for part in diarizer.process(audio).sort_by_start_time()
    ]


def speaker_label(start, end, turns):
    minimum = min(0.12, max(0.01, (end - start) / 2))
    active = [
        (max(start, a), min(end, b), speaker)
        for a, b, speaker in turns
        if min(end, b) - max(start, a) >= minimum
    ]
    labels = sorted({speaker for _, _, speaker in active})
    if not labels:
        return "[speaker uncertain]"
    if len(labels) == 1:
        coverage = sum(b - a for a, b, _ in active)
        if coverage >= (end - start) / 2:
            return f"Speaker {labels[0]}:"
        return "[speaker uncertain]"
    for i, (a, b, speaker) in enumerate(active):
        if any(other != speaker and min(b, d) - max(a, c) >= minimum
               for c, d, other in active[i + 1:]):
            return "[overlapping speech]"
    return "[multiple speaker turns]"
