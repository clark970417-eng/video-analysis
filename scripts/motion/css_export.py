"""Export observed translation and scale as sampled CSS keyframes."""
from itertools import groupby
from math import hypot


def export_css(elements):
    lines = [
        "/* Sampled video motion. Use a centered transform origin and original-size elements.",
        "   Durations and delays use the video timeline. Hidden gaps have no keyframes. */",
    ]
    summaries = []
    for name, element in elements.items():
        origin = element["path"][0]
        for number, group in groupby(element["path"], key=lambda point: point["segment"]):
            path = list(group)
            start = path[0]["time_s"]
            duration = path[-1]["time_s"] - start
            if duration <= 0:
                continue
            identifier = f"video-motion-{name.replace('_', '-')}-{number}"
            lines.append(f"\n@keyframes {identifier} {{")
            position_error = scale_error = time_error = 0.0
            for sample in path:
                offset = sample["time_s"] - start
                percent = round(100 * offset / duration, 9)
                x = sample["x_px"] - origin["x_px"]
                y = sample["y_px"] - origin["y_px"]
                scale = sample["scale_ratio"]
                rx, ry, rs = round(x, 4), round(y, 4), round(scale, 6)
                position_error = max(position_error, hypot(x - rx, y - ry))
                scale_error = max(scale_error, abs(scale - rs))
                time_error = max(time_error, abs(offset - percent * duration / 100))
                lines.append(f"  {percent:.9f}% {{ transform: translate({rx}px, {ry}px) scale({rs}); }}")
            fill = "both" if len(element["segments"]) == 1 and element["status"] == "tracked" else "none"
            lines.extend([
                "}", f".{identifier} {{", "  transform-origin: center;",
                f"  animation: {identifier} {duration:.9f}s linear {start:.9f}s {fill};", "}",
            ])
            summaries.append(dict(
                animation=identifier, start_s=start, duration_s=duration,
                max_keyframe_position_rounding_px=position_error,
                max_keyframe_scale_rounding=scale_error,
                max_keyframe_time_rounding_s=time_error + 0.000000001,
            ))
    return "\n".join(lines) + "\n", summaries
