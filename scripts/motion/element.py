"""Track a selected rectangle by fitting a similarity transform to its features."""
import cv2
import numpy as np


def tracking_luma(gray):
    # Normalize local contrast for tracking. Keep raw luma for appearance measurements.
    image = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    mean = cv2.blur(image, (15, 15))
    variance = np.maximum(cv2.blur(image * image, (15, 15)) - mean * mean, 4)
    return np.uint8(np.clip(128 + 32 * (image - mean) / np.sqrt(variance), 0, 255))


def motion_summary(path, tolerance, hold, complete):
    if len(path) < 2:
        return None
    points = np.array([[sample["x_px"], sample["y_px"]] for sample in path])
    times = np.array([sample["time_s"] for sample in path])
    # Ignore sub-tolerance zigzags from resampling and optical-flow jitter.
    simplified = cv2.approxPolyDP(points.astype(np.float32), tolerance / 2, False)
    distance = float(cv2.arcLength(simplified, False))
    displacement = points[-1] - points[0]
    net = float(np.linalg.norm(displacement))
    departed = np.flatnonzero(np.linalg.norm(points - points[0], axis=1) > tolerance)
    onset = int(departed[0]) if departed.size else None
    settling = arrival = overshoot = crossings = None
    tail = times >= times[-1] - hold
    if complete and onset is not None and times[-1] - times[0] >= hold:
        destination = np.median(points[tail], axis=0)
        outside = np.flatnonzero(np.linalg.norm(points - destination, axis=1) > tolerance)
        settled_index = int(outside[-1] + 1) if outside.size else 0
        if settled_index < len(times) and times[-1] - times[settled_index] >= hold:
            settling = float(times[settled_index])
            axis = destination - points[0]
            length = np.linalg.norm(axis)
            if length > tolerance:
                projection = (points - destination) @ (axis / length)
                reached = np.flatnonzero(np.linalg.norm(points - destination, axis=1) <= tolerance)
                arrival = float(times[reached[0]]) if reached.size else None
                overshoot = float(max(0.0, projection.max()))
                sides = np.sign(projection[np.abs(projection) > tolerance])
                crossings = int(np.count_nonzero(sides[1:] != sides[:-1]))
    return dict(
        onset_s=float(times[onset]) if onset is not None else None,
        first_arrival_s=arrival, settled_s=settling,
        onset_to_settled_s=settling - times[onset] if settling is not None else None,
        distance_traveled_px=distance if onset is not None else 0.0, net_displacement_px=net,
        overshoot_px=overshoot, destination_crossings=crossings,
        tolerance_px=tolerance, required_hold_s=hold,
    )


class ElementTracker:
    def __init__(self, box, recovery_window):
        self.box = box
        self.path = []
        self.loss_s = None
        self.recovery_window = recovery_window
        self.gaps = []
        self.segment = 1

    def lose(self, time):
        if self.path and self.loss_s is None:
            self.loss_s = time

    def seed(self, gray, features):
        x, y, w, h = self.box
        height, width = gray.shape
        left, top = round(x * width), round(y * height)
        right, bottom = round((x + w) * width), round((y + h) * height)
        self.reference = gray[top:bottom, left:right].copy()
        if min(self.reference.shape) < 12:
            raise ValueError("Element box must be at least 12 by 12 pixels at analysis resolution")
        self.points = self.features_in_box(features, (left, top, right, bottom))
        if self.points is None or len(self.points) < 6:
            raise ValueError("Element has too few distinct features. Select a textured element or a clearer start frame")
        self.center = np.array([(left + right - 1) / 2, (top + bottom - 1) / 2])
        self.offset = np.array([left, top])
        self.transform = np.eye(3)
        self.shape = gray.shape
        self.reference_luma = cv2.GaussianBlur(self.reference, (5, 5), 0).astype(float).ravel()
        self.reference_luma -= self.reference_luma.mean()

    def features_in_box(self, features, box):
        left, top, right, bottom = box
        mask = np.zeros_like(features)
        mask[top + 2:bottom - 2, left + 2:right - 2] = 255
        return cv2.goodFeaturesToTrack(
            features, maxCorners=200, qualityLevel=0.01, minDistance=3, mask=mask
        )

    def recover(self, gray, features):
        # Search around the last reliable location using the original appearance.
        # Matching is invariant to luma offset and contrast, but assumes little rotation.
        scale = np.linalg.norm(self.transform[:2, 0])
        center = self.transform[:2, :2] @ self.center + self.transform[:2, 2]
        h, w = self.reference.shape
        radius = int(max(3 * max(w, h) * scale, 0.2 * max(gray.shape)))
        x0, y0 = np.maximum(0, np.floor(center - radius)).astype(int)
        x1, y1 = np.minimum(gray.shape[::-1], np.ceil(center + radius)).astype(int)
        search = gray[y0:y1, x0:x1]
        matches = []
        for factor in (0.8, 0.9, 1.0, 1.1, 1.25):
            size = (round(w * scale * factor), round(h * scale * factor))
            if min(size) < 12 or size[0] > search.shape[1] or size[1] > search.shape[0]:
                continue
            template = cv2.resize(self.reference, size, interpolation=cv2.INTER_LINEAR)
            scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
            _, confidence, _, (x, y) = cv2.minMaxLoc(scores)
            matches.append((confidence, x, y, size, scores))
        if not matches:
            return False
        confidence, x, y, (width, height), _ = max(matches, key=lambda match: match[0])
        if confidence < 0.85:
            return False
        for _, _, _, (mw, mh), scores in matches:
            # Ignore overlapping hypotheses for the same element, including nearby scales.
            cx = x + (width - mw) // 2
            cy = y + (height - mh) // 2
            lx, rx = max(0, cx - width // 2), min(scores.shape[1], cx + width // 2 + 1)
            ty, by = max(0, cy - height // 2), min(scores.shape[0], cy + height // 2 + 1)
            if lx < rx and ty < by:
                scores[ty:by, lx:rx] = -1
            if confidence - float(scores.max()) < 0.08:
                return False
        left, top = x0 + x, y0 + y
        points = self.features_in_box(features, (left, top, left + width, top + height))
        if points is None or len(points) < 6:
            return False
        recovered_scale = (width / w + height / h) / 2
        self.transform = np.eye(3)
        self.transform[:2, :2] *= recovered_scale
        self.transform[:2, 2] = [left, top] - recovered_scale * self.offset
        self.points = points
        return True

    def estimate(self, previous, current):
        moved, forward, _ = cv2.calcOpticalFlowPyrLK(previous, current, self.points, None)
        if moved is None:
            return None
        back, reverse, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None)
        if back is None:
            return None
        errors = np.linalg.norm((back - self.points).reshape(-1, 2), axis=1)
        usable = (forward.ravel() == 1) & (reverse.ravel() == 1) & (errors < 1.2)
        if usable.sum() < 6:
            return None
        transform, inliers = cv2.estimateAffinePartial2D(
            self.points[usable], moved[usable], ransacReprojThreshold=1.5
        )
        if transform is None:
            return None
        keep = inliers.ravel().astype(bool)
        if keep.sum() < 6 or keep.sum() < len(self.points) * 0.4:
            return None
        self.points = moved[usable][keep]
        return np.vstack((transform, [0, 0, 1]))

    def appearance(self, current):
        h, w = self.reference.shape
        matrix = self.transform[:2].copy()
        matrix[:, 2] += matrix[:, :2] @ self.offset
        corners = np.array([[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]])
        mapped = corners @ matrix[:, :2].T + matrix[:, 2]
        height, width = current.shape
        if np.any(mapped.min(axis=0) < -0.5) or np.any(mapped.max(axis=0) > [width - 0.5, height - 0.5]):
            return None
        aligned = cv2.warpAffine(current, matrix, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        luma = cv2.GaussianBlur(aligned, (5, 5), 0).astype(float).ravel()
        mean = float(luma.mean())
        luma -= mean
        reference_energy = np.dot(self.reference_luma, self.reference_luma)
        energy = np.dot(luma, luma)
        if reference_energy <= 0 or energy <= 0:
            return None
        covariance = np.dot(self.reference_luma, luma)
        correlation = covariance / np.sqrt(reference_energy * energy)
        if correlation < 0.55:
            return None
        return dict(mean_luma=mean, contrast_ratio=float(covariance / reference_energy),
                    alignment_score=float(np.clip(correlation, 0, 1)))

    def measure(self, previous, current, time, source_scale):
        if self.loss_s is not None and time - self.loss_s > self.recovery_window:
            return
        features = tracking_luma(current)
        if not self.path:
            self.seed(current, features)
        elif self.loss_s is None:
            delta = None
            if previous is not None and current.shape == self.shape:
                delta = self.estimate(self.previous_features, features)
            if delta is None:
                self.lose(time)
                return
            self.transform = delta @ self.transform
        else:
            if not self.recovery_window or current.shape != self.shape or time <= self.path[-1]["time_s"]:
                return
            if not self.recover(current, features):
                return

        appearance = self.appearance(current)
        if appearance is None:
            if not self.path:
                raise ValueError("Could not establish the element appearance at the start frame")
            self.lose(time)
            return
        recovered = self.loss_s is not None
        if recovered:
            self.gaps.append(dict(last_tracked_s=self.path[-1]["time_s"],
                                  lost_at_s=self.loss_s, resumed_at_s=time))
            self.loss_s = None
            self.segment += 1
        x, y = (self.transform[:2, :2] @ self.center + self.transform[:2, 2]) * source_scale
        speed = None
        if self.path and not recovered:
            last = self.path[-1]
            speed = float(np.hypot(x - last["x_px"], y - last["y_px"]) / (time - last["time_s"]))
        self.previous_features = features
        self.path.append(dict(
            time_s=time, segment=self.segment, x_px=float(x), y_px=float(y), speed_px_s=speed,
            scale_ratio=float(np.linalg.norm(self.transform[:2, 0])),
            **appearance,
        ))

    def report(self, tolerance, hold):
        changes = {}
        if self.path:
            for field in ("scale_ratio", "mean_luma", "contrast_ratio"):
                low = min(self.path, key=lambda sample: sample[field])
                high = max(self.path, key=lambda sample: sample[field])
                changes[field] = dict(
                    start=self.path[0][field], end=self.path[-1][field],
                    min=low[field], min_at_s=low["time_s"],
                    max=high[field], max_at_s=high["time_s"],
                )
        segments = []
        start = 0
        ends = [i for i in range(1, len(self.path))
                if self.path[i]["segment"] != self.path[i - 1]["segment"]]
        for end in ends + [len(self.path)]:
            samples = self.path[start:end]
            complete = end == len(self.path) and self.loss_s is None
            segments.append(dict(
                segment=samples[0]["segment"], start_s=samples[0]["time_s"],
                end_s=samples[-1]["time_s"],
                motion=motion_summary(samples, tolerance, hold, complete),
            ))
            start = end
        return dict(
            initial_box=self.box, status="lost" if self.loss_s is not None else "tracked",
            lost_at_s=self.loss_s, gaps=self.gaps, segments=segments, path=self.path,
            appearance_and_scale=changes, motion=segments[0]["motion"] if len(segments) == 1 else None,
        )
