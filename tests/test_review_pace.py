"""Coaching must remain useful without turning uncertain ASR into a quality score."""
import copy
import unittest

from src.review_analysis import build_review


class ReviewPaceTests(unittest.TestCase):
    def setUp(self):
        self.lines = [
            '안녕하세요 오늘은 발표를 준비하면서 겪는 어려움을 함께 살펴보겠습니다.',
            '혼자서 연습할 때에는 자신의 말하기 습관을 객관적으로 확인하기 어렵습니다.',
            '저희는 촬영한 영상을 다시 보면서 고칠 부분을 쉽게 찾도록 만들었습니다.',
            '마지막으로 핵심 내용을 간단히 정리하고 앞으로의 개발 계획을 설명하겠습니다.',
        ]
        self.raw = {'duration': 40, 'segments': [
            {'start': 1, 'end': 9, 'text': self.lines[0]},
            {'start': 10, 'end': 18, 'text': self.lines[1]},
            {'start': 19, 'end': 27, 'text': self.lines[2]},
            {'start': 28, 'end': 36, 'text': self.lines[3]},
        ]}
        self.script = '\n'.join(self.lines)

    def test_consistent_pace_is_not_certified_good_without_external_reference(self):
        pace = build_review(self.raw, self.script)['pace']
        self.assertEqual(pace['status'], 'reference_only')
        self.assertEqual(pace['reference']['kind'], 'recording_median')
        self.assertIsNone(pace['reference']['target'])
        self.assertIn('판단할 수 없습니다', pace['summary'])
        self.assertEqual(pace['intervals'], [])
        self.assertNotIn('score', pace)

    def test_local_rushed_group_has_a_seekable_feedback_interval(self):
        self.raw['segments'][-1]['end'] = 31
        pace = build_review(self.raw, self.script)['pace']
        self.assertEqual(pace['status'], 'needs_review')
        fast = [item for item in pace['intervals'] if item['kind'] == 'fast']
        self.assertEqual(len(fast), 1)
        self.assertEqual((fast[0]['start'], fast[0]['end']), (28, 31))
        self.assertEqual(fast[0]['script_index'], 4)
        self.assertTrue(fast[0]['detail'])

    def test_bad_script_does_not_produce_speed_feedback_from_unrelated_asr(self):
        self.raw['segments'][-1]['end'] = 31
        report = build_review(self.raw, '흰 구름 아래 따뜻한 햇살과 나무가 있습니다.')
        self.assertEqual(report['pace']['status'], 'insufficient_evidence')
        self.assertEqual(report['pace']['usable_group_count'], 0)
        self.assertFalse(any(item['kind'] in ('fast', 'slow') for item in report['pace']['intervals']))
        self.assertFalse(any('빠른 편' in note for s in report['segments'] for note in s['notes']))
        self.assertEqual(report['comparison'][0]['status'], 'unconfirmed')

    def test_target_assesses_whole_talk_separately_from_local_pace(self):
        pace = build_review(self.raw, self.script, target_seconds=60)['pace']
        self.assertEqual(pace['reference']['target']['status'], 'shorter')
        self.assertEqual(pace['reference']['target']['actual_seconds'], 35)
        self.assertEqual(pace['intervals'], [])
        self.assertEqual(pace['status'], 'needs_review')
        self.assertEqual(pace['label'], '전체 시간 확인 필요')

    def test_target_does_not_turn_unrecognized_script_into_fast_speech(self):
        script = self.script + '\n' + '대본에만 있는 다른 주제와 설명이 이어집니다.' * 8
        pace = build_review(self.raw, script, target_seconds=60)['pace']
        self.assertEqual(pace['reference']['target']['status'], 'unconfirmed')
        self.assertLess(pace['script_coverage'], .85)

    def test_long_unrelated_addition_prevents_target_completion_claim(self):
        raw = copy.deepcopy(self.raw)
        raw['duration'] = 60
        raw['segments'].append({'start': 38, 'end': 58, 'text': '모두 다른 이야기입니다.' * 15})
        pace = build_review(raw, self.script, target_seconds=60)['pace']
        self.assertEqual(pace['reference']['target']['status'], 'unconfirmed')

    def test_matching_plan_has_no_delivery_quality_score(self):
        pace = build_review(self.raw, self.script, target_seconds=35)['pace']
        self.assertEqual(pace['reference']['target']['status'], 'near_target')
        self.assertEqual(pace['status'], 'reference_only')
        self.assertIn('절대 기준', pace['reference']['details'])

    def test_english_and_numbers_excluded_even_after_manual_transcript_edit(self):
        mixed_line = '이제 QCS6490 보드에서 동작하도록 만드는 것이 저희의 목표입니다.'
        self.raw['segments'][-1]['end'] = 31
        script = '\n'.join(self.lines[:3] + [mixed_line])
        pace = build_review(self.raw, script, edits={'4': mixed_line})['pace']
        measurement = pace['measurements'][-1]
        self.assertEqual(measurement['status'], 'excluded')
        self.assertIn('영문·숫자', measurement['reason'])
        self.assertFalse(any(item.get('script_index') == 4 and item['kind'] == 'fast'
                             for item in pace['intervals']))

    def test_manually_corrected_transcript_can_restore_comparison_without_overwriting_raw(self):
        original = copy.deepcopy(self.raw)
        self.raw['segments'][1]['text'] = '알 수 없는 다른 말입니다.'
        before = build_review(self.raw, self.script)['pace']
        after = build_review(self.raw, self.script, edits={'2': self.lines[1]})
        self.assertGreater(after['pace']['usable_group_count'], before['usable_group_count'])
        self.assertEqual(after['segments'][1]['asr_text'], '알 수 없는 다른 말입니다.')
        self.assertNotEqual(self.raw, original)
        self.assertFalse(after['training_eligible'])

    def test_group_rate_includes_observed_internal_pause_and_reports_it(self):
        raw = {'duration': 14, 'segments': [
            {'start': 1, 'end': 5, 'text': '질문의 핵심과 발표 자료를 바탕으로'},
            {'start': 7, 'end': 12, 'text': '답변에 필요한 내용을 발표자에게 보여줍니다.'},
        ]}
        script = '질문의 핵심과 발표 자료를 바탕으로 답변에 필요한 내용을 발표자에게 보여줍니다.'
        pace = build_review(raw, script)['pace']
        self.assertEqual(pace['measurements'][0]['duration'], 11)
        self.assertEqual(pace['pausing']['long_internal_pause_count'], 1)
        pause = next(item for item in pace['intervals'] if item['kind'] == 'pause')
        self.assertEqual((pause['start'], pause['end']), (5, 7))
        self.assertIn('잘못 끊었다는 뜻은 아닙니다', pause['detail'])

    def test_short_speech_is_insufficient_and_silence_has_no_judgment(self):
        short = build_review({'duration': 3, 'segments': [{'start': 0, 'end': 2, 'text': '안녕하세요.'}]},
                             '안녕하세요.', target_seconds=30)['pace']
        self.assertEqual(short['status'], 'insufficient_evidence')
        self.assertEqual(short['reference']['target']['status'], 'unconfirmed')
        silent = build_review({'duration': 3, 'segments': []}, self.script, 30)['pace']
        self.assertEqual(silent['status'], 'no_speech')
        self.assertEqual(silent['intervals'], [])

    def test_old_reports_can_be_rebuilt_without_new_raw_fields(self):
        raw = copy.deepcopy(self.raw)
        report = build_review(raw)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['pace']['version'], 1)
        self.assertEqual(raw, self.raw)
        self.assertTrue(report['pace']['measurements'])


if __name__ == '__main__':
    unittest.main()
