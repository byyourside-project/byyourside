import copy
import unittest

from src.review_analysis import build_review, script_lines


class ReviewAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.raw = {'duration': 20, 'segments': [
            {'start': 1, 'end': 5, 'text': '발표를 시작합니다.'},
            {'start': 7, 'end': 11, 'text': '질문의 핵심과 발표 자료를 바탕으로'},
            {'start': 12, 'end': 15, 'text': '답변에 필요한 내용을 보여줍니다.'},
        ]}

    def test_no_plan_does_not_judge_schedule(self):
        report = build_review(self.raw)
        self.assertEqual(report['schedule']['status'], 'no_plan')
        self.assertIsNone(report['schedule']['difference_seconds'])
        self.assertFalse(report['training_eligible'])

    def test_target_compares_first_to_last_speech_including_internal_pauses(self):
        report = build_review(self.raw, target_seconds=10)
        self.assertEqual(report['schedule']['status'], 'over_target')
        self.assertEqual(report['schedule']['actual_seconds'], 14)
        self.assertEqual(report['schedule']['difference_seconds'], 4)

    def test_silence_has_no_rate_or_completion_judgment(self):
        report = build_review({'duration': 10, 'segments': []}, '인사합니다.', 30)
        self.assertIsNone(report['summary']['rate'])
        self.assertEqual(report['schedule']['status'], 'no_speech')
        self.assertEqual(report['comparison'][0]['status'], 'unconfirmed')

    def test_one_script_group_can_span_two_speech_segments(self):
        script = '발표를 시작합니다.\n질문의 핵심과 발표 자료를 바탕으로 답변에 필요한 내용을 보여줍니다.'
        report = build_review(self.raw, script)
        self.assertEqual([row['segment_ids'] for row in report['comparison']], [[1], [2, 3]])
        self.assertEqual(report['comparison'][1]['internal_pauses'][0]['duration'], 1)

    def test_explicit_script_groups_preserved(self):
        self.assertEqual(script_lines('안녕하세요. 소개합니다.\n다음 내용입니다.'),
                         ['안녕하세요. 소개합니다.', '다음 내용입니다.'])
        self.assertEqual(script_lines('소개합니다. 다음 내용입니다.'), ['소개합니다.', '다음 내용입니다.'])

    def test_repeated_script_does_not_reuse_one_utterance(self):
        raw = {'duration': 8, 'segments': [{'start': 1, 'end': 6, 'text': '발표를 시작합니다.'}]}
        report = build_review(raw, '발표를 시작합니다.\n발표를 시작합니다.')
        ids = [i for row in report['comparison'] for i in row['segment_ids']]
        self.assertEqual(ids, [1])
        self.assertEqual(sum(row['status'] == 'unconfirmed' for row in report['comparison']), 1)

    def test_unmatched_content_is_not_reported_as_proven_omission(self):
        report = build_review(self.raw, '구름 사이 햇살이 따뜻해요.')
        self.assertEqual(report['comparison'][0]['status'], 'unconfirmed')
        self.assertEqual(report['comparison'][0]['segment_ids'], [])

    def test_edit_preserves_original_and_updates_rate(self):
        original = copy.deepcopy(self.raw)
        report = build_review(self.raw, edits={'1': '안녕'})
        segment = report['segments'][0]
        self.assertEqual(segment['text'], '안녕')
        self.assertEqual(segment['asr_text'], '발표를 시작합니다.')
        self.assertEqual(segment['rate'], 30)
        self.assertEqual(self.raw, original)
        self.assertEqual(report['review_state'], 'user_edited_transcript')
        self.assertFalse(report['training_eligible'])

    def test_reference_does_not_replace_automatic_transcript(self):
        report = build_review(self.raw, '다른 대본입니다.')
        self.assertEqual(report['segments'][0]['text'], self.raw['segments'][0]['text'])

    def test_invalid_target_rejected(self):
        for value in (True, 0, -1, 7201, float('nan'), float('inf'), 'not a number'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_review(self.raw, target_seconds=value)

    def test_invalid_segment_boundaries_rejected(self):
        for start, end in ((-1, 2), (2, 2), (4, 3), (1, 21), (float('nan'), 2)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                build_review({'duration': 20, 'segments': [{'start': start, 'end': end, 'text': '말'}]})
        raw = copy.deepcopy(self.raw)
        raw['segments'][1]['start'] = 4
        with self.assertRaises(ValueError):
            build_review(raw)

    def test_invalid_edits_rejected(self):
        for edits in ([], False, {'99': '없음'}, {'1': None}, {'1': '가' * 2001}):
            with self.subTest(edits=type(edits)), self.assertRaises(ValueError):
                build_review(self.raw, edits=edits)

    def test_relative_speed_requires_comparable_segments(self):
        raw = {'duration': 30, 'segments': [
            {'start': 0, 'end': 5, 'text': '가' * 20},
            {'start': 6, 'end': 11, 'text': '나' * 20},
            {'start': 12, 'end': 17, 'text': '다' * 20},
            {'start': 18, 'end': 23, 'text': '라' * 40},
            {'start': 24, 'end': 29, 'text': '마' * 50 + 'QCS6490'},
        ]}
        report = build_review(raw)
        self.assertTrue(any('빠른 편' in note for note in report['segments'][3]['notes']))
        self.assertFalse(any('빠른 편' in note for note in report['segments'][4]['notes']))


if __name__ == '__main__':
    unittest.main()
