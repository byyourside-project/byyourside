"""Behavioral checks for uncertainty, timing and the camera-facing proxy."""
import math
from pathlib import Path
import tempfile
import unittest

from src.review_gaze import (aggregate_samples, analyze_gaze, classify_geometry,
                             extract_geometry, gaze_options)


def face_points():
    points = [{'x': .5, 'y': .5} for _ in range(478)]
    for index, x, y in ((10, .5, .2), (152, .5, .8), (234, .25, .5), (454, .75, .5),
                        (127, .28, .3), (356, .72, .3),
                        (33, .32, .4), (133, .44, .4), (159, .38, .38), (145, .38, .42),
                        (362, .56, .4), (263, .68, .4), (386, .62, .38), (374, .62, .42),
                        (468, .38, .4), (473, .62, .4)):
        points[index] = {'x': x, 'y': y}
    return points


def matrix(yaw=0, pitch=0):
    yaw, pitch = math.radians(yaw), math.radians(pitch)
    return [[math.cos(yaw), math.sin(yaw) * math.sin(pitch), math.sin(yaw) * math.cos(pitch), 0],
            [0, math.cos(pitch), -math.sin(pitch), 0],
            [-math.sin(yaw), math.cos(yaw) * math.sin(pitch), math.cos(yaw) * math.cos(pitch), 0],
            [0, 0, 0, 1]]


class GeometryTests(unittest.TestCase):
    def test_front_camera_and_head_turn(self):
        points = face_points()
        geometry, reason = extract_geometry(points, matrix(), 640, 480)
        self.assertIsNone(reason)
        self.assertEqual(classify_geometry(geometry), ('camera_facing', 'camera_facing'))
        geometry, _ = extract_geometry(points, matrix(yaw=35), 640, 480)
        self.assertEqual(classify_geometry(geometry), ('away', 'head_away'))
        geometry, _ = extract_geometry(points, matrix(pitch=-30), 640, 480)
        self.assertEqual(classify_geometry(geometry), ('away', 'head_away'))

    def test_eyes_move_without_head_turn(self):
        points = face_points()
        points[468]['x'] += .025
        points[473]['x'] += .025
        geometry, reason = extract_geometry(points, matrix(), 640, 480)
        self.assertIsNone(reason)
        self.assertEqual(classify_geometry(geometry), ('away', 'eyes_away'))

    def test_blink_is_unknown_not_away_or_good(self):
        points = face_points()
        for i in (159, 145, 386, 374):
            points[i]['y'] = .4
        geometry, reason = extract_geometry(points, matrix(), 640, 480)
        self.assertIsNone(geometry)
        self.assertEqual(reason, 'poor_visibility')

    def test_tiny_clipped_missing_and_nonfinite_faces_unknown(self):
        points = face_points()
        self.assertEqual(extract_geometry(points, matrix(), 100, 100)[1], 'small_face')
        points[10]['y'] = 0
        self.assertEqual(extract_geometry(points, matrix(), 640, 480)[1], 'poor_visibility')
        self.assertEqual(extract_geometry(face_points()[:468], matrix(), 640, 480)[1], 'no_geometry')
        points = face_points()
        points[468]['x'] = float('nan')
        self.assertEqual(extract_geometry(points, matrix(), 640, 480)[1], 'no_geometry')
        self.assertEqual(extract_geometry(face_points(), None, 640, 480)[1], 'no_geometry')

    def test_ambiguous_threshold_is_unknown(self):
        self.assertEqual(classify_geometry({'yaw': 21, 'pitch': 0, 'iris_x': 0, 'iris_y': 0}),
                         ('unknown', 'boundary'))
        self.assertEqual(classify_geometry({'yaw': float('nan')}), ('unknown', 'no_geometry'))

    def test_explicit_baseline_applies_only_when_passed(self):
        position = {'yaw': 28, 'pitch': 0, 'iris_x': 0, 'iris_y': 0}
        self.assertEqual(classify_geometry(position)[0], 'away')
        self.assertEqual(classify_geometry(position, {'yaw': 10, 'pitch': 0, 'iris_x': 0, 'iris_y': 0})[0],
                         'camera_facing')


class AggregationTests(unittest.TestCase):
    def test_all_unknown_never_produces_good_score(self):
        report = aggregate_samples([{'time': i / 2, 'status': 'unknown', 'reason': 'no_face'} for i in range(10)], 5)
        self.assertIsNone(report['camera_facing_ratio'])
        self.assertEqual(report['status'], 'insufficient_evidence')
        self.assertEqual(report['coverage_ratio'], 0)
        self.assertEqual(report['unknown_seconds'], 5)

    def test_time_weighting_and_partial_last_bucket(self):
        samples = [{'time': i / 2, 'status': 'camera_facing' if i < 6 else 'away',
                    'reason': 'camera_facing' if i < 6 else 'head_away'} for i in range(9)]
        report = aggregate_samples(samples, 4.2)
        self.assertAlmostEqual(report['camera_facing_ratio'], 3 / 4.2, places=4)
        self.assertEqual(report['analyzed_seconds'], 4.2)
        self.assertEqual(report['away_seconds'], 1.2)
        self.assertEqual(report['events'], [])

    def test_unknown_excluded_denominator_and_breaks_event(self):
        samples = [{'time': i / 2, 'status': 'away', 'reason': 'head_away'} for i in range(10)]
        samples[4] = {'time': 2, 'status': 'unknown', 'reason': 'multiple_faces'}
        report = aggregate_samples(samples, 5)
        self.assertEqual(report['coverage_ratio'], .9)
        self.assertEqual(report['camera_facing_ratio'], 0)
        self.assertEqual([(p['start'], p['end']) for p in report['events']], [(0, 2), (2.5, 5)])

    def test_missing_frames_do_not_extend_good_or_away_intervals(self):
        report = aggregate_samples([{'time': 0, 'status': 'away', 'reason': 'head_away'},
                                    {'time': 4, 'status': 'camera_facing', 'reason': 'camera_facing'}], 5)
        self.assertEqual(report['analyzed_seconds'], 1)
        self.assertEqual(report['unknown_seconds'], 4)
        self.assertEqual(report['longest_away_seconds'], .5)
        self.assertIsNone(report['camera_facing_ratio'])

    def test_changed_away_cause_still_one_event(self):
        samples = [{'time': i / 2, 'status': 'away', 'reason': 'head_away' if i < 3 else 'eyes_away'}
                   for i in range(10)]
        report = aggregate_samples(samples, 5)
        self.assertEqual(len(report['events']), 1)
        self.assertEqual(report['longest_away_seconds'], 5)

    def test_invalid_options_and_sample_times_rejected(self):
        for options in ({'sample_fps': True}, {'sample_fps': float('nan')}, {'sample_fps': 100},
                        {'calibration_seconds': -1}, [], {'sustained_seconds': 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                gaze_options(options)
        with self.assertRaises(ValueError):
            aggregate_samples([{'time': .5}, {'time': 0}], 5)
        with self.assertRaises(ValueError):
            aggregate_samples([], float('inf'))

    def test_missing_model_is_explicit_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            result = analyze_gaze(Path(directory) / 'missing.mp4', directory, 5)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'model_missing')
        self.assertIsNone(result['camera_facing_ratio'])
        self.assertEqual(result['events'], [])


if __name__ == '__main__':
    unittest.main()
