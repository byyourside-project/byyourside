"""Delayed model results advance completed content without reviving corrections."""
import copy
import unittest

from src.presentation import Session
from src.script_coaching import ScriptSession, prepare_script
from tests.test_presentation import FakeClock


TEXTS = ['서비스는 기기 안에서 실행합니다.', '마이크는 발표자의 음성을 입력받습니다.', '발표 시간에 맞춰 안내합니다.']
DECK = {'deck_id': 'inflight', 'title': '이어지는 발표', 'total_duration_sec': 120,
        'slides': [{'slide_id': 'same', 'title': '발표', 'target_duration_sec': 120,
                    'keypoints': [{'keypoint_id': f'p{i+1}', 'text': text, 'required': True}
                                  for i,text in enumerate(TEXTS)]}]}


class _JudgmentFixtures:
    def setUp(self):
        self.clock = FakeClock()
        self.session = Session(DECK, clock=self.clock)

    def feed(self, text, start, end, sid, endpoint='silence', status='OK'):
        self.clock.value = max(self.clock.value, self.session.origin + end + .1)
        return self.session.ingest({'segment_id':sid, 'text':text, 'start_sec':start, 'end_sec':end,
                                    'endpoint_reason':endpoint, 'status':status})

    def response(self, job, states):
        """states maps point id to (status, evidence, optional reason_code)."""
        judgments = []
        for point in job['slide']['keypoints']:
            state = states.get(point['keypoint_id'], ('unconfirmed', []))
            item = {'keypoint_id':point['keypoint_id'], 'status':state[0],
                    'evidence_segment_ids':state[1], 'reason':'시험 근거 판단'}
            if len(state)>2:
                item['reason_code']=state[2]
            judgments.append(item)
        return {'judgments':judgments}


class InflightJudgmentTests(_JudgmentFixtures, unittest.TestCase):
    def test_inflight_first_sentence_is_applied_while_second_request_stays_pending(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        second = self.feed(TEXTS[1], 7, 12, 'second')
        self.assertFalse(self.session.job_is_current(first))
        self.assertTrue(self.session.job_is_relevant(first))
        applied = self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertIn('p1', [j['keypoint_id'] for j in applied])
        self.assertEqual(self.session.states['p1']['status'], 'explained')
        self.assertEqual(self.session.states['p2']['status'], 'unconfirmed')
        self.assertTrue(self.session.revisions[1]['pending'])
        self.assertEqual(self.session.snapshot()['judgment_status'], 'evaluating')
        self.session.apply(second, self.response(second, {'p1':('explained',['first']), 'p2':('explained',['second'])}))
        self.assertFalse(self.session.revisions[1]['pending'])
        self.assertEqual(self.session.states['p2']['status'], 'explained')

    def test_next_unfinished_sentence_does_not_hide_completed_first_sentence(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed('마이크는 발표자의', 7, 9, 'cut', endpoint='hard_max_duration')
        self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertEqual(self.session.states['p1']['status'], 'explained')
        self.assertEqual(self.session.states['p2']['status'], 'unconfirmed')
        self.assertEqual(self.session.snapshot()['judgment_status'], 'waiting_for_silence')

    def test_incomplete_old_evidence_cannot_be_confirmed_over_its_continuation(self):
        first = self.feed('서비스는 기기 안에서', 1, 3, 'fragment')
        newest = self.feed('실행하지 않습니다.', 3.4, 5, 'continuation')
        self.session.apply(first, self.response(first, {'p1':('explained',['fragment'])}))
        self.assertEqual(self.session.states['p1']['status'], 'unconfirmed')
        self.assertTrue(any(e.get('reason')=='newer_utterance_continuation' for e in self.session.events))
        self.session.apply(newest, self.response(newest, {'p1':('uncertain',['fragment','continuation'])}))
        self.assertEqual(self.session.states['p1']['status'], 'uncertain')

    def test_explicit_new_correction_defers_old_positive(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed('아니요, 앞선 설명을 정정합니다. 외부 서버에서 실행합니다.', 7, 9, 'correction')
        self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertEqual(self.session.states['p1']['status'], 'unconfirmed')
        self.assertTrue(self.session.revisions[1]['pending'])
        self.assertTrue(any(e.get('reason')=='newer_correction_pending' for e in self.session.events))

    def test_implicit_new_description_of_same_point_defers_old_positive(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed('서비스는 외부 서버에서 실행합니다.', 7, 9, 'changed')
        self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertEqual(self.session.states['p1']['status'], 'unconfirmed')
        self.assertTrue(any(e.get('reason')=='newer_related_speech_pending' for e in self.session.events))

    def test_short_refutations_that_do_not_repeat_point_words_hold_old_positive(self):
        for correction in ('아닙니다.', '아니, 앞에 말한 것과 달라요.', '그렇지 않습니다.', '아뇨.'):
            with self.subTest(correction=correction):
                self.setUp()
                first=self.feed(TEXTS[0],1,6,'first')
                self.feed(correction,7,9,'refutation')
                self.session.apply(first,self.response(first,{'p1':('explained',['first'])}))
                self.assertEqual(self.session.states['p1']['status'],'unconfirmed')
                self.assertTrue(self.session.revisions[1]['pending'])

    def test_older_positive_cannot_revive_newer_uncertain_judgment(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        newest = self.feed(TEXTS[1], 7, 12, 'second')
        self.session.apply(newest, self.response(newest, {'p1':('uncertain',['first','second']), 'p2':('explained',['second'])}))
        before = copy.deepcopy(self.session.states)
        self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertEqual(self.session.states, before)
        self.assertTrue(any(e.get('reason')=='newer_judgment_applied' for e in self.session.events))

    def test_older_uncertain_cannot_downgrade_newer_explained_judgment(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        newest = self.feed(TEXTS[1], 7, 12, 'second')
        self.session.apply(newest, self.response(newest, {'p1':('explained',['first']), 'p2':('explained',['second'])}))
        before = copy.deepcopy(self.session.states)
        self.session.apply(first, self.response(first, {'p1':('uncertain',['first'])}))
        self.assertEqual(self.session.states, before)

    def test_new_absence_does_not_erase_real_older_evidence(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        newest = self.feed(TEXTS[1], 7, 12, 'second')
        self.session.apply(newest, self.response(newest, {'p2':('explained',['second'])}))
        self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertEqual(self.session.states['p1']['status'], 'explained')
        self.assertEqual(self.session.states['p2']['status'], 'explained')
        self.assertFalse(self.session.revisions[1]['pending'])

    def test_newer_audio_error_blocks_old_positive_even_without_new_revision(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed('', 7, 9, 'lost', status='ERROR')
        self.session.apply(first, self.response(first, {'p1':('explained',['first'])}))
        self.assertEqual(self.session.states['p1']['status'], 'uncertain')
        self.assertTrue(any(e.get('reason')=='newer_audio_quality' for e in self.session.events))

    def test_old_failure_does_not_clear_latest_pending_or_create_quality_issue(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed(TEXTS[1], 7, 12, 'second')
        self.session.fail_job(first, 'old timeout')
        self.assertTrue(self.session.revisions[1]['pending'])
        self.assertEqual(self.session.issues, [])

    def test_invalid_old_response_is_validated_before_any_partial_state_change(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed(TEXTS[1], 7, 12, 'second')
        response=self.response(first, {'p1':('explained',['first']), 'p2':('explained',['invented'])})
        before=copy.deepcopy(self.session.states)
        with self.assertRaises(ValueError):
            self.session.apply(first, response)
        self.assertEqual(self.session.states, before)
        self.assertTrue(self.session.revisions[1]['pending'])

    def test_job_cannot_replace_real_transcript_or_timestamps(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        for field,value in (('text','조작된 발화'),('end_sec',5)):
            with self.subTest(field=field):
                changed=copy.deepcopy(first)
                changed['segments'][0][field]=value
                with self.assertRaises(ValueError):
                    self.session.apply(changed,self.response(changed,{'p1':('explained',['first'])}))
                self.assertEqual(self.session.states['p1']['status'],'unconfirmed')

    def test_finished_request_cannot_apply_twice_or_fail_after_success(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        response=self.response(first, {'p1':('explained',['first'])})
        self.session.apply(first,response)
        before=copy.deepcopy(self.session.states)
        self.session.apply(first,response)
        self.session.fail_job(first,'late callback failure')
        self.assertEqual(self.session.states,before)
        self.assertEqual(self.session.issues,[])

    def test_end_of_session_still_rejects_even_valid_older_response(self):
        first = self.feed(TEXTS[0], 1, 6, 'first')
        self.feed(TEXTS[1], 7, 12, 'second')
        self.session.finish()
        before=copy.deepcopy(self.session.states)
        self.session.apply(first,self.response(first,{'p1':('explained',['first'])}))
        self.assertEqual(self.session.states,before)


class ScriptInflightJudgmentTests(_JudgmentFixtures, unittest.TestCase):
    def setUp(self):
        self.clock=FakeClock()
        self.session=ScriptSession(prepare_script('\n'.join(TEXTS),120),clock=self.clock)

    # Reuse source helpers, not the generic-session cases with p1/p2 ids.
    def test_inflight_first_sentence_updates_audio_anchor_but_not_pace_before_latest_result(self):
        first=self.feed(TEXTS[0],1,16,'first')
        second=self.feed(TEXTS[1],17,22,'second')
        self.session.apply(first,self.response(first,{'script-1':('explained',['first'])}))
        progress=self.session.progress()
        self.assertEqual(progress['position_index'],0)
        self.assertEqual(progress['measured_elapsed_sec'],16)
        self.assertFalse(progress['reliable'])
        self.assertEqual(progress['pace'],'waiting')
        self.assertTrue(self.session.revisions[1]['pending'])
        self.session.apply(second,self.response(second,{'script-1':('explained',['first']),'script-2':('explained',['second'])}))
        self.assertEqual(self.session.confirmed_audio_ends,{'script-1':16,'script-2':22})
        self.assertTrue(self.session.progress()['reliable'])

    def test_paraphrased_negation_holds_old_positive_until_newest_cumulative_judgment(self):
        original='음성 인식은 로컬에서 실행합니다.'
        for correction in ('음성 인식은 기기 내부에서 처리하지 않습니다.',
                           '음성 인식은 기기 내부에서 처리하지 못합니다.',
                           '음성 인식은 기기 내부에서 못 합니다.',
                           '음성 인식은 기기 내부에서 안 처리합니다.',
                           '음성 인식은 기기 내부에서 안됩니다.'):
            with self.subTest(correction=correction):
                self.session=ScriptSession(prepare_script(original+'\n다음으로 발표 시간을 확인합니다.',120),clock=self.clock)
                old=self.feed(original,1,4,'old')
                latest=self.feed(correction,5,7,'new')
                self.session.apply(old,self.response(old,{'script-1':('explained',['old'])}))
                self.assertEqual(self.session.states['script-1']['status'],'unconfirmed')
                self.assertEqual(self.session.confirmed_audio_ends,{})
                self.assertTrue(self.session.revisions[1]['pending'])
                self.assertTrue(any(e.get('reason')=='newer_negation_pending' for e in self.session.events))
                self.session.apply(latest,self.response(latest,{'script-1':('uncertain',['new'])}))
                self.assertEqual(self.session.states['script-1']['status'],'uncertain')
                self.assertFalse(self.session.revisions[1]['pending'])
                self.assertFalse(self.session.progress()['reliable'])

    def test_normal_negative_clause_only_holds_old_result_and_latest_can_confirm_both_points(self):
        original='음성 인식은 로컬에서 실행합니다.'
        following='음성을 외부 서버로 보내지 않고 기기 안에서 처리합니다.'
        self.session=ScriptSession(prepare_script(original+'\n'+following,120),clock=self.clock)
        old=self.feed(original,1,4,'old')
        latest=self.feed(following,5,7,'next')
        self.session.apply(old,self.response(old,{'script-1':('explained',['old'])}))
        self.assertEqual(self.session.states['script-1']['status'],'unconfirmed')
        self.assertTrue(self.session.revisions[1]['pending'])
        applied=self.session.apply(latest,self.response(latest,{'script-1':('explained',['old']),'script-2':('explained',['next'])}))
        self.assertEqual({j['keypoint_id'] for j in applied},{'script-1','script-2'})
        self.assertEqual(self.session.progress()['fraction'],1)
        self.assertEqual(self.session.confirmed_audio_ends,{'script-1':4,'script-2':7})
        self.assertFalse(self.session.revisions[1]['pending'])

    def test_stt_inserted_spaces_in_refutations_still_hold_old_positive(self):
        original='음성 인식은 로컬에서 실행합니다.'
        for correction in ('아 닙니다.', '아 뇨.', '처리하지 않 습니다.', '안 합 니다.', '못 합 니다.',
                           '그 렇 지 않 습 니 다.', '정 정 합 니 다.'):
            with self.subTest(correction=correction):
                self.session=ScriptSession(prepare_script(original+'\n다음으로 발표 시간을 확인합니다.',120),clock=self.clock)
                old=self.feed(original,1,4,'old')
                latest=self.feed(correction,5,7,'new')
                self.session.apply(old,self.response(old,{'script-1':('explained',['old'])}))
                self.assertEqual(self.session.states['script-1']['status'],'unconfirmed')
                self.assertEqual(self.session.confirmed_audio_ends,{})
                self.assertTrue(self.session.revisions[1]['pending'])
                self.assertTrue(any(e.get('reason') in ('newer_negation_pending','newer_correction_pending')
                                    for e in self.session.events))
                self.session.apply(latest,self.response(latest,{'script-1':('uncertain',['new'])}))
                self.assertEqual(self.session.states['script-1']['status'],'uncertain')
                self.assertFalse(self.session.revisions[1]['pending'])

    def test_location_inside_device_is_not_misread_as_negative_adverb(self):
        original=TEXTS[0]
        following='발표 자료는 기기 안에서 안정적으로 관리합니다.'
        self.session=ScriptSession(prepare_script(original+'\n'+following,120),clock=self.clock)
        old=self.feed(original,1,4,'old')
        self.feed(following,5,7,'next')
        self.session.apply(old,self.response(old,{'script-1':('explained',['old'])}))
        self.assertEqual(self.session.states['script-1']['status'],'explained')
        self.assertTrue(self.session.revisions[1]['pending'])

    def test_script_request_exposes_confirmed_metadata_without_mutating_deck(self):
        first=self.feed(TEXTS[0],1,16,'first')
        self.assertTrue(first['script_tracking'])
        self.assertEqual(first['confirmed_keypoint_ids'],[])
        self.session.apply(first,self.response(first,{'script-1':('explained',['first'])}))
        second=self.feed(TEXTS[1],17,22,'second')
        self.assertEqual(second['confirmed_keypoint_ids'],['script-1'])
        self.assertEqual(len(self.session.deck['slides'][0]['keypoints']),3)

    def test_partial_later_anchors_do_not_claim_missing_until_newest_request_finishes(self):
        later=self.feed(TEXTS[2],1,16,'later')
        newest=self.feed(TEXTS[0],17,22,'first')
        self.session.apply(later,self.response(later,{'script-3':('explained',['later'])}))
        self.assertEqual(self.session.missing_ids,[])
        self.assertFalse(any(a['key'].startswith('script_missing:') for a in self.session.snapshot()['alerts']))
        self.session.apply(newest,self.response(newest,{'script-1':('explained',['first']),'script-3':('explained',['later'])}))
        self.assertEqual(self.session.missing_ids,['script-2'])

    def test_pending_new_evidence_holds_existing_missing_then_restores_if_still_missing(self):
        later=self.feed(TEXTS[2],1,16,'later')
        self.session.apply(later,self.response(later,{'script-3':('explained',['later'])}))
        self.assertEqual(self.session.missing_ids,['script-1','script-2'])
        newest=self.feed(TEXTS[0],17,22,'first')
        self.assertEqual(self.session.missing_ids,[])
        self.assertFalse(any(a['key'].startswith('script_missing:') for a in self.session.snapshot()['alerts']))
        self.session.apply(newest,self.response(newest,{'script-1':('explained',['first']),'script-3':('explained',['later'])}))
        self.assertEqual(self.session.missing_ids,['script-2'])

    def test_completed_request_with_no_changed_judgments_restores_earlier_missing_section(self):
        texts=[f'발표 구간 {i}의 연구 결과를 설명합니다.' for i in range(1,8)]
        self.session=ScriptSession(prepare_script('\n'.join(texts),120),clock=self.clock)
        covered=self.feed(' '.join(texts[1:]),1,39,'covered')
        response=self.response(covered,{f'script-{i}':('explained',['covered']) for i in range(2,8)})
        self.session.apply(covered,response)
        # Initial routing intentionally examines a bounded forward range. The
        # final sentence supplies the last anchor through its own normal job.
        final=self.feed(texts[-1],39.2,40,'final')
        self.session.apply(final,self.response(final,{'script-7':('explained',['final'])}))
        self.assertEqual(self.session.missing_ids,['script-1'])
        closing=self.feed('그럼 발표를 끝냅니다.',41,45,'closing')
        self.assertEqual(self.session.missing_ids,[])
        self.assertNotIn('script-1',[p['keypoint_id'] for p in closing['slide']['keypoints']])
        self.assertTrue(all(self.session.states[p['keypoint_id']]['status']=='explained'
                            for p in closing['slide']['keypoints']))
        unchanged=self.response(closing,{})
        self.assertEqual(self.session.apply(closing,unchanged),[])
        self.assertFalse(self.session.revisions[1]['pending'])
        self.assertEqual(self.session.missing_ids,['script-1'])
        self.assertTrue(any(a['key'].startswith('script_missing:script-1') for a in self.session.snapshot()['alerts']))

    def test_rejected_stale_or_duplicate_result_does_not_restart_script_review(self):
        completed=self.feed(TEXTS[0],1,16,'first')
        response=self.response(completed,{'script-1':('explained',['first'])})
        self.session.apply(completed,response)
        latest=self.feed(TEXTS[1],17,22,'second')
        # These observable sentinels represent the current pending display; an
        # old completed request must not perform another review or pace update.
        from unittest.mock import patch
        with patch.object(self.session,'_review_script') as review, patch.object(self.session,'_update_pace') as pace:
            self.assertEqual(self.session.apply(completed,response),[])
            foreign=copy.deepcopy(latest)
            foreign['session_id']='other-session'
            self.assertEqual(self.session.apply(foreign,self.response(foreign,{})),[])
            review.assert_not_called()
            pace.assert_not_called()
        self.assertTrue(self.session.revisions[1]['pending'])

    def test_invalid_current_result_does_not_perform_script_review_or_clear_pending(self):
        latest=self.feed(TEXTS[0],1,16,'first')
        invalid=self.response(latest,{'script-1':('explained',['invented'])})
        from unittest.mock import patch
        with patch.object(self.session,'_review_script') as review, patch.object(self.session,'_update_pace') as pace:
            with self.assertRaises(ValueError):
                self.session.apply(latest,invalid)
            review.assert_not_called()
            pace.assert_not_called()
        self.assertTrue(self.session.revisions[1]['pending'])
