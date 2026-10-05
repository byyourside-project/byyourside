"""Sentence/display timing uses fake clocks and explicit semantic evidence only."""
import copy
import unittest

from src.script_coaching import ScriptSession, prepare_script
from tests.test_presentation import FakeClock


TEXTS = ['연구 목적과 준비 과정을 소개합니다.', '마이크가 발표자의 음성을 입력받습니다.',
         '공유기로 기기의 네트워크를 연결합니다.', '화면에서 발표 자료를 확인합니다.',
         '스피커가 속도 안내를 전달합니다.', '보드에서 프로그램을 실행합니다.']


class SentencePacingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.session = ScriptSession(prepare_script('가나다라.\n마바사아.', 80), clock=self.clock)

    def replace_script(self, texts, duration=120):
        self.session = ScriptSession(prepare_script('\n'.join(texts), duration), clock=self.clock)

    def feed(self, text, end, sid, *, start=None, completed_at=None, endpoint='silence', status='OK'):
        at = end + .1 if completed_at is None else completed_at
        self.clock.value = max(self.clock.value, self.session.origin + at)
        return self.session.ingest({'segment_id':sid, 'text':text,
                                    'start_sec':max(0,end-2) if start is None else start,
                                    'end_sec':end, 'endpoint_reason':endpoint, 'status':status})

    @staticmethod
    def response(job, judgments):
        values = []
        for point in job['slide']['keypoints']:
            status,evidence = judgments.get(point['keypoint_id'], ('unconfirmed', []))
            values.append({'keypoint_id':point['keypoint_id'], 'status':status,
                           'evidence_segment_ids':evidence, 'reason':'고정된 시험 근거'})
        return {'judgments':values}

    def confirm(self, index, end, completed_at, sid=None, evidence=None):
        point=self.session.plan['units'][index]
        sid=sid or f'unit-{index+1}-{end}'
        job=self.feed(point['text'],end,sid,completed_at=completed_at)
        self.session.apply(job,self.response(job,{point['keypoint_id']:('explained',evidence or [sid])}))
        return job

    def unit(self, index):
        return self.session.progress()['units'][index]

    def display(self):
        return self.session.progress()['display_pace']

    def test_weighted_plan_adds_duration_without_changing_allocations(self):
        deck=prepare_script('가나다.\n마바사아자.',80)
        units=deck['script_plan']['units']
        self.assertEqual([p['planned_duration_sec'] for p in units],[30,50])
        self.assertEqual([p['planned_start_sec'] for p in units],[0,30])
        self.assertEqual([p['planned_end_sec'] for p in units],[30,80])
        self.assertEqual(sum(p['planned_duration_sec'] for p in units),80)

    def test_first_confirmed_sentence_is_classified_immediately_without_voice_gate(self):
        self.confirm(0,10,14)
        row=self.unit(0)
        self.assertEqual(row['planned_duration_sec'],40)
        self.assertEqual(row['completed_at_sec'],14)
        self.assertEqual(row['evidence_audio_end_sec'],10)
        self.assertEqual(row['processing_delay_sec'],4)
        self.assertEqual(row['adjusted_completed_at_sec'],10)
        self.assertEqual(row['actual_duration_sec'],10)
        self.assertEqual(row['sentence_ratio'],4)
        self.assertEqual(row['sentence_pace'],'fast')
        self.assertFalse(row['timing_estimated'])
        display=self.display()
        self.assertEqual(display['pace'],'fast')
        self.assertTrue(display['reliable'])
        self.assertEqual(display['estimated_total_sec'],20)
        self.assertEqual(display['latest_keypoint_id'],'script-1')
        # Voice guidance still requires its existing sample/stability policy.
        self.assertEqual(self.session.progress()['pace'],'waiting')
        self.assertFalse(any(a['key'].startswith('pace:') for a in self.session.alerts))

    def test_exact_twenty_five_percent_sentence_boundaries_are_on_plan(self):
        for ratio,expected in ((.75,'on_plan'),(1.25,'on_plan'),(.7,'slow'),(1.3,'fast')):
            with self.subTest(ratio=ratio):
                self.setUp()
                end=40/ratio
                self.confirm(0,end,end+2)
                self.assertAlmostEqual(self.unit(0)['sentence_ratio'],ratio)
                self.assertEqual(self.unit(0)['sentence_pace'],expected)
                self.assertEqual(self.display()['pace'],expected)

    def test_sentence_duration_uses_adjacent_adjusted_endpoints_not_summed_delays(self):
        self.confirm(0,10,20)
        self.confirm(1,30,45)
        first,second=self.session.progress()['units']
        self.assertEqual(first['processing_delay_sec'],10)
        self.assertEqual(second['processing_delay_sec'],15)
        self.assertEqual(second['actual_duration_sec'],20)
        self.assertEqual(second['sentence_ratio'],2)
        self.assertEqual(second['sentence_pace'],'fast')
        self.assertEqual(self.display()['measured_elapsed_sec'],30)
        self.assertEqual(self.display()['processing_delay_sec'],15)

    def test_repeat_keeps_first_confirmation_and_endpoint(self):
        self.confirm(0,10,14)
        original=copy.deepcopy(self.session.sentence_timings['script-1'])
        self.confirm(0,40,50,sid='repeat')
        self.assertEqual(self.session.sentence_timings['script-1'],original)
        self.assertEqual(self.unit(0)['actual_duration_sec'],10)
        self.assertEqual(self.display()['measured_elapsed_sec'],10)
        self.assertEqual(self.display()['processing_delay_sec'],4)
        self.assertEqual(self.session.confirmed_audio_ends['script-1'],10)

    def test_revocation_removes_timing_and_reconfirmation_records_new_first_completion(self):
        self.confirm(0,10,14)
        corrected=self.feed('기존 내용을 정정합니다.',20,'correction',completed_at=24)
        self.session.apply(corrected,self.response(corrected,{'script-1':('uncertain',['correction'])}))
        self.assertNotIn('script-1',self.session.sentence_timings)
        self.assertIsNone(self.unit(0)['completed_at_sec'])
        self.assertFalse(self.display()['reliable'])
        self.confirm(0,30,35,sid='reconfirmed')
        self.assertEqual(self.unit(0)['completed_at_sec'],35)
        self.assertEqual(self.unit(0)['adjusted_completed_at_sec'],30)
        self.assertEqual(self.unit(0)['processing_delay_sec'],5)

    def test_latest_model_failure_prunes_revoked_timing_immediately(self):
        self.confirm(0,10,14)
        latest=self.feed('앞서 설명을 정정합니다.',20,'correction',completed_at=21)
        self.session.fail_job(latest,'내용 판단 오류')
        self.assertEqual(self.session.states['script-1']['status'],'uncertain')
        self.assertNotIn('script-1',self.session.sentence_timings)
        self.assertIsNone(self.unit(0)['completed_at_sec'])
        self.confirm(0,30,32,sid='reconfirmed')
        self.assertEqual(self.unit(0)['completed_at_sec'],32)
        self.assertEqual(self.display()['reason_code'],'record_quality_issue')

    def test_invalid_response_is_atomic_for_existing_and_new_sentence_timings(self):
        self.confirm(0,10,14)
        original=copy.deepcopy(self.session.sentence_timings)
        latest=self.feed('마바사아.',20,'second',completed_at=23)
        with self.assertRaises(ValueError):
            self.session.apply(latest,self.response(latest,{'script-2':('explained',['invented'])}))
        self.assertEqual(self.session.sentence_timings,original)
        self.assertTrue(self.display()['pending'])

    def test_duplicate_and_foreign_result_do_not_replace_first_timing(self):
        original_job=self.confirm(0,10,14)
        original=copy.deepcopy(self.session.sentence_timings)
        latest=self.feed('마바사아.',20,'second',completed_at=23)
        self.session.apply(original_job,self.response(original_job,{'script-1':('explained',['unit-1-10'])}))
        foreign=copy.deepcopy(latest)
        foreign['session_id']='other'
        self.session.apply(foreign,self.response(foreign,{'script-2':('explained',['second'])}))
        self.assertEqual(self.session.sentence_timings,original)
        self.assertTrue(self.display()['pending'])

    def test_old_inflight_first_confirmation_is_visible_while_latest_job_stays_pending(self):
        old=self.feed('가나다라.',10,'first',completed_at=10.1)
        self.feed('마바사아.',20,'second',completed_at=22)
        self.session.apply(old,self.response(old,{'script-1':('explained',['first'])}))
        display=self.display()
        self.assertEqual(display['completed_at_sec'],22)
        self.assertEqual(display['processing_delay_sec'],12)
        self.assertEqual(display['pace'],'fast')
        self.assertTrue(display['reliable'])
        self.assertTrue(display['pending'])
        self.assertEqual(display['reason_code'],'processing_pending')
        self.assertFalse(self.session.progress()['reliable'])

    def test_late_board_point_with_old_evidence_uses_request_end_for_timing_only(self):
        self.replace_script(TEXTS,120)
        for index,end in enumerate((7.5,15.61,22,29,35)):
            self.confirm(index,end,end+1,sid=f'evidence-{index+1}')
        job=self.confirm(5,42.805,44.67,sid='board',evidence=['evidence-2'])
        row=self.unit(5)
        self.assertEqual(self.session.states['script-6']['status'],'explained')
        self.assertEqual(self.session.states['script-6']['evidence_segment_ids'],['evidence-2'])
        self.assertEqual(row['evidence_audio_end_sec'],15.61)
        self.assertEqual(row['adjusted_completed_at_sec'],42.805)
        self.assertEqual(row['timing_basis'],'request_speech_end')
        self.assertTrue(row['timing_estimated'])
        self.assertAlmostEqual(row['processing_delay_sec'],1.865)
        self.assertAlmostEqual(row['actual_duration_sec'],7.805)
        self.assertEqual(self.session.confirmed_audio_ends['script-6'],15.61)
        self.assertEqual(self.display()['measured_elapsed_sec'],42.805)
        self.assertEqual(self.display()['latest_keypoint_id'],'script-6')
        self.assertTrue(self.display()['reliable'])
        self.assertFalse(self.session.progress()['reliable'])
        self.assertEqual(max(s['end_sec'] for s in job['segments']),42.805)

    def test_fallback_from_old_inflight_result_never_uses_newer_live_endpoint(self):
        self.replace_script(TEXTS[:4],120)
        self.confirm(0,5,6,sid='first')
        self.confirm(1,10,11,sid='second')
        old=self.feed(TEXTS[2],20,'third',completed_at=21)
        self.feed(TEXTS[3],30,'fourth',completed_at=32)
        self.session.apply(old,self.response(old,{'script-3':('explained',['first'])}))
        self.assertEqual(self.latest_endpoint(),30)
        self.assertEqual(self.unit(2)['adjusted_completed_at_sec'],20)
        self.assertEqual(self.unit(2)['timing_basis'],'request_speech_end')
        self.assertEqual(self.unit(2)['processing_delay_sec'],12)
        self.assertEqual(self.display()['measured_elapsed_sec'],20)
        self.assertTrue(self.display()['pending'])

    def latest_endpoint(self):
        return self.session.latest_audio_end

    def test_earlier_missed_point_backfill_preserves_its_evidence_and_latest_position(self):
        self.feed('가나다라.',10,'earlier',completed_at=11)
        self.confirm(1,20,22,sid='second')
        self.assertFalse(self.display()['reliable'])
        latest=self.feed('그럼 발표를 마칩니다.',30,'closing',completed_at=32)
        self.session.apply(latest,self.response(latest,{'script-1':('explained',['earlier'])}))
        first,second=self.session.progress()['units']
        self.assertEqual(first['adjusted_completed_at_sec'],10)
        self.assertFalse(first['timing_estimated'])
        self.assertEqual(first['processing_delay_sec'],22)
        self.assertEqual(second['actual_duration_sec'],10)
        self.assertEqual(self.display()['latest_keypoint_id'],'script-2')
        self.assertEqual(self.display()['measured_elapsed_sec'],20)
        self.assertTrue(self.display()['reliable'])

    def test_missing_adjacent_sentence_withholds_duration_and_display(self):
        self.replace_script(TEXTS[:3],90)
        self.confirm(0,10,11,sid='first')
        self.confirm(2,30,32,sid='third')
        self.assertIsNone(self.unit(2)['actual_duration_sec'])
        self.assertIsNone(self.unit(2)['sentence_ratio'])
        self.assertEqual(self.unit(2)['sentence_pace'],'waiting')
        self.assertEqual(self.display()['reason_code'],'unresolved_script_gap')
        self.assertFalse(self.display()['reliable'])

    def test_any_uncertain_point_withholds_display_even_with_a_known_first_sentence(self):
        self.confirm(0,10,14)
        latest=self.feed('다음 설명은 확인이 필요합니다.',20,'unknown',completed_at=22)
        self.session.apply(latest,self.response(latest,{'script-2':('uncertain',['unknown'])}))
        self.assertEqual(self.display()['pace'],'waiting')
        self.assertEqual(self.display()['reason_code'],'uncertain_judgment')
        self.assertFalse(self.display()['reliable'])

    def test_capture_and_model_issues_withhold_display_without_faking_new_completion(self):
        for issue in ('음성 입력 손실 감지','내용 판단 오류'):
            with self.subTest(issue=issue):
                self.setUp()
                self.confirm(0,10,14)
                original=copy.deepcopy(self.session.sentence_timings)
                self.session.issue(issue)
                self.assertEqual(self.session.sentence_timings,original)
                self.assertEqual(self.display()['reason_code'],'record_quality_issue')
                self.assertFalse(self.display()['reliable'])

    def test_no_first_sentence_distinguishes_initial_waiting_from_input_error(self):
        self.assertEqual(self.display()['reason_code'],'no_confirmed_sentence')
        self.assertIsNone(self.display()['latest_keypoint_id'])
        self.session.issue('마이크 입력 손실')
        self.assertEqual(self.display()['reason_code'],'record_quality_issue')
        self.assertIsNone(self.display()['latest_keypoint_id'])

    def test_pending_unrelated_speech_and_stale_age_retain_last_completed_numbers(self):
        self.confirm(0,10,14)
        before=self.display()
        latest=self.feed('잠시 발표 준비 상태를 확인하겠습니다.',30,'unrelated',completed_at=32)
        pending=self.display()
        self.assertEqual(pending['ratio'],before['ratio'])
        self.assertEqual(pending['measured_elapsed_sec'],before['measured_elapsed_sec'])
        self.assertEqual(pending['completed_at_sec'],before['completed_at_sec'])
        self.assertTrue(pending['reliable'])
        self.assertTrue(pending['pending'])
        self.session.apply(latest,self.response(latest,{}))
        self.clock.advance(20)
        self.session.tick()
        after=self.display()
        self.assertEqual(after['pace'],before['pace'])
        self.assertEqual(after['estimated_total_sec'],before['estimated_total_sec'])
        self.assertEqual(after['processing_delay_sec'],4)
        self.assertEqual(after['reason_code'],'stale_snapshot')
        self.assertTrue(after['reliable'])
        self.assertFalse(after['pending'])
        self.assertEqual(self.session.progress()['pace'],'waiting')

    def test_ended_session_retains_last_known_final_values(self):
        self.confirm(0,10,14)
        self.confirm(1,20,23)
        before=self.display()
        self.session.finish()
        self.clock.advance(100)
        after=self.display()
        for key in ('pace','measured_elapsed_sec','planned_elapsed_sec','ratio','estimated_total_sec',
                    'processing_delay_sec','completed_at_sec'):
            self.assertEqual(after[key],before[key])
        self.assertTrue(after['final'])
        self.assertTrue(after['reliable'])
        self.assertFalse(after['pending'])
        self.assertEqual(after['reason_code'],'final_snapshot')

    def test_inflight_completion_after_stop_records_real_clock_and_keeps_speech_endpoint(self):
        job=self.feed('가나다라.',10,'first',completed_at=11)
        self.clock.value=self.session.origin+12
        self.session.stop()
        self.clock.value=self.session.origin+20
        self.session.apply(job,self.response(job,{'script-1':('explained',['first'])}))
        row=self.unit(0)
        self.assertEqual(self.session.elapsed(),12)
        self.assertEqual(row['completed_at_sec'],20)
        self.assertEqual(row['processing_delay_sec'],10)
        self.assertEqual(row['adjusted_completed_at_sec'],10)
        self.session.finish()
        self.assertEqual(self.display()['completed_at_sec'],20)
        self.assertEqual(self.display()['processing_delay_sec'],10)
        self.assertTrue(self.display()['final'])

    def test_one_shared_speech_endpoint_has_no_invented_zero_second_ratio(self):
        job=self.feed('가나다라. 마바사아.',10,'both',completed_at=14)
        self.session.apply(job,self.response(job,{'script-1':('explained',['both']),'script-2':('explained',['both'])}))
        self.assertEqual(self.unit(1)['actual_duration_sec'],0)
        self.assertIsNone(self.unit(1)['sentence_ratio'])
        self.assertEqual(self.unit(1)['sentence_pace'],'waiting')
        self.assertTrue(self.display()['reliable'])
        self.assertEqual(self.display()['measured_elapsed_sec'],10)

    def test_out_of_order_backfill_with_later_evidence_withholds_display(self):
        self.confirm(1,20,22,sid='second')
        self.confirm(0,35,38,sid='late-first')
        self.assertEqual(self.unit(0)['adjusted_completed_at_sec'],35)
        self.assertEqual(self.unit(1)['adjusted_completed_at_sec'],20)
        self.assertIsNone(self.unit(1)['actual_duration_sec'])
        self.assertEqual(self.display()['latest_keypoint_id'],'script-2')
        self.assertEqual(self.display()['reason_code'],'unordered_completion_times')
        self.assertFalse(self.display()['reliable'])


if __name__ == '__main__':
    unittest.main()
