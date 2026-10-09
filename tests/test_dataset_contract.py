"""Synthetic fixtures only: contract, isolation, deterministic split and adapter parity."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.datasets.common import DatasetError, OllamaCoach, SYSTEM_PROMPT, SCHEMA, digest, load_jsonl, model_input, record_errors, require_valid, schema_errors, validate_rows, write_json, write_jsonl
from scripts.datasets.audit_duplicates import audit, legacy_inputs, require_release_audit
from scripts.datasets.build_splits import build_splits
from scripts.datasets.export_sft import export_sft, load_release
from scripts.datasets.export_eval import export_eval
from src.coaching_eval import validate_cases, evaluate_cases
from src.presentation import PhraseCoach

ROOT = Path(__file__).resolve().parents[1]


def fixture(index=0):
    text = hashlib.sha256(str(index).encode()).hexdigest() + ' ' + hashlib.sha256(f'fact{index}'.encode()).hexdigest()
    sid = f'sample-{index}'
    segment = {'segment_id': f'{sid}-seg', 'text': text, 'start_sec': 0.0, 'end_sec': 1.0, 'status': 'OK', 'endpoint_reason': 'silence'}
    return {'schema_version': '1.0', 'sample_id': sid, 'task': 'judge_keypoint_semantics', 'function_version': '1.0',
            'source': {'kind': 'synthetic', 'source_id': sid, 'repository_commit': '53cce3436355f36aa2edb1f45500f984de07594a',
                       'session_id': sid, 'deck_family_id': sid, 'speaker_group_id': sid, 'scenario_family_id': sid,
                       'parent_sample_id': None, 'derived_from_ids': [], 'consent_status': 'synthetic_no_personal_data', 'pii_status': 'cleared'},
            'snapshot': {'phase': 'ongoing', 'visit_version': 1, 'revision': 1, 'cutoff_sec': 1.0, 'pending': False,
                         'quality_issue_codes': [], 'context_trimmed': False, 'segments': [segment]},
            'input': {'slide_title': '합성 테스트', 'keypoints': {'1': text}, 'utterances': [{'number': 1, 'text': text}]},
            'mapping': {'keypoint_ids': {'1': 'original-k1'}, 'utterance_segment_ids': {'1': [segment['segment_id']]}},
            'target': {'compact': {'1': {'s': 1, 'e': [1]}}},
            'annotation': {'status': 'adjudicated', 'annotator_ids': ['synthetic-reviewer'], 'guideline_version': '1.0',
                           'point_details': {'1': {'relation': 'equivalent', 'rationale': 'fixture-only exact fact'}}, 'confidence': 'high'},
            'lineage': {'split_group_id': sid, 'duplicate_cluster_id': None, 'transform_version': '1.0', 'prompt_version': OllamaCoach.prompt_version},
            'split': 'unassigned'}


class DatasetContractTests(unittest.TestCase):
    def test_valid_record(self):
        self.assertEqual(validate_rows([fixture()]), [])

    def test_bad_json_duplicate_keys_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.jsonl'
            for text in ('{', '{"sample_id":1,"sample_id":2}', '{"value":NaN}', '', '\n'):
                path.write_text(text, encoding='utf-8')
                with self.assertRaises(DatasetError):
                    load_jsonl(path)

    def test_invalid_label_bool_and_evidence(self):
        for label in ({'s': 2, 'e': [1]}, {'s': True, 'e': [1]}, {'s': 1, 'e': [99]},
                      {'s': 1, 'e': []}, {'s': -1, 'e': [1]}, {'s': 0, 'e': [True]}, {'s': 1, 'e': [1, 1]}):
            row = fixture(); row['target']['compact']['1'] = label
            self.assertTrue(validate_rows([row]))

    def test_schema_changes_cannot_silently_bypass_validator(self):
        with self.assertRaises(DatasetError):
            schema_errors({}, {'type': 'object', 'anyOf': []})

    def test_unicode_normalization_preserves_distinct_negation(self):
        from scripts.datasets.common import normalize
        import unicodedata
        self.assertEqual(normalize('가나다'), normalize(unicodedata.normalize('NFD', '가나다')))
        self.assertNotEqual(normalize('실행합니다'), normalize('실행하지 않습니다'))

    def test_sft_rejects_conflicting_targets_without_writing(self):
        a, b = fixture(), fixture(1)
        b['input'] = copy.deepcopy(a['input']); b['snapshot'] = copy.deepcopy(a['snapshot'])
        b['mapping'] = copy.deepcopy(a['mapping']); b['target']['compact']['1']['s'] = 0
        a['split'] = b['split'] = 'train'
        with self.assertRaises(DatasetError):
            export_sft([a, b])

    def test_missing_fields_extra_model_metadata_and_wrong_types(self):
        for change in ('missing', 'metadata', 'list'):
            row = fixture()
            if change == 'missing':
                del row['mapping']
            elif change == 'metadata':
                row['input']['expected_status'] = 'explained'
            else:
                row['input']['keypoints'] = []
            self.assertTrue(validate_rows([row]))

    def test_duplicate_sample_id_and_wrong_keypoint(self):
        self.assertTrue(validate_rows([fixture(), fixture()]))
        row = fixture(); row['target']['compact'] = {'2': {'s': 1, 'e': [1]}}
        self.assertTrue(validate_rows([row]))

    def test_future_timestamp_and_fabricated_merged_text(self):
        row = fixture(); row['snapshot']['segments'][0]['end_sec'] = 2
        self.assertTrue(validate_rows([row]))
        row = fixture(); row['input']['utterances'][0]['text'] = 'future conclusion injected'
        self.assertTrue(validate_rows([row]))
        row = fixture(); row['snapshot']['segments'][0]['start_sec'] = 2
        self.assertTrue(validate_rows([row]))

    def test_runtime_merge_parity_and_original_mapping(self):
        row = fixture()
        first = row['snapshot']['segments'][0]
        second = dict(first, segment_id='seg-2', start_sec=1.5, end_sec=2.0, text='마지막 설명입니다.')
        row['snapshot']['segments'].append(second); row['snapshot']['cutoff_sec'] = 2
        row['input']['utterances'] = [{'number': 1, 'text': first['text'] + ' ' + second['text']}]
        row['mapping']['utterance_segment_ids']['1'].append('seg-2')
        self.assertFalse(validate_rows([row]))
        job = {'slide': {'title': row['input']['slide_title'], 'keypoints': [{'keypoint_id': 'original-k1', 'text': row['input']['keypoints']['1']}]}, 'segments': row['snapshot']['segments']}
        coach = OllamaCoach('fixture:1b'); coach.model_info = {'name': 'fixture:1b'}
        captured = []
        def request(url, body):
            captured.append(json.loads(body))
            return {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(row['target']['compact'])}}
        with patch.object(coach, '_request', side_effect=request):
            out = coach.evaluate(job)
        self.assertEqual(json.loads(captured[0]['messages'][1]['content']), row['input'])
        self.assertEqual(out['judgments'][0]['evidence_segment_ids'], [first['segment_id'], 'seg-2'])
        self.assertEqual(captured[0]['format']['required'], list(row['target']['compact']))

    def test_unreviewed_quality_and_consent_block_release(self):
        for field in ('review', 'consent', 'pii', 'pending', 'stt'):
            row = fixture(); row['split'] = 'train'
            if field == 'review': row['annotation']['status'] = 'draft'
            if field == 'consent': row['source']['consent_status'] = 'denied'
            if field == 'pii': row['source']['pii_status'] = 'present'
            if field == 'pending': row['snapshot']['pending'] = True
            if field == 'stt': row['snapshot']['segments'][0]['status'] = 'UNCERTAIN'
            with self.assertRaises(DatasetError): export_sft([row])

    def test_personal_data_hint_requires_quarantine(self):
        row = fixture()
        row['input']['keypoints']['1'] = '연락처 test@example.com'
        self.assertTrue(validate_rows([row]))
        row['split'] = 'quarantine'
        self.assertFalse(validate_rows([row]))

    def test_conflicting_targets_isolated(self):
        left, right = fixture(), fixture(1)
        right['input'] = copy.deepcopy(left['input'])
        right['snapshot'] = copy.deepcopy(left['snapshot']); right['mapping'] = copy.deepcopy(left['mapping'])
        right['target']['compact']['1'] = {'s': 0, 'e': [1]}
        self.assertEqual(audit([left, right])['pairs'][0]['kind'], 'conflict')
        rows, _, _ = build_splits([left, right])
        self.assertEqual({r['split'] for r in rows}, {'quarantine'})

    def test_normalized_duplicates_detected(self):
        left, right = fixture(), fixture(1)
        right['input'] = copy.deepcopy(left['input']); right['snapshot'] = copy.deepcopy(left['snapshot']); right['mapping'] = copy.deepcopy(left['mapping'])
        right['input']['slide_title'] = '  ' + left['input']['slide_title'] + '  '
        self.assertEqual(audit([left, right])['pairs'][0]['kind'], 'normalized_input')

    def test_group_parent_and_speaker_connections_not_split(self):
        rows = [fixture(i) for i in range(20)]
        rows[1]['source']['session_id'] = rows[0]['source']['session_id']
        rows[2]['source']['parent_sample_id'] = rows[1]['sample_id']
        rows[3]['source']['speaker_group_id'] = rows[2]['source']['speaker_group_id']
        result, _, _ = build_splits(rows)
        self.assertEqual(len({r['split'] for r in result if r['sample_id'] in {rows[i]['sample_id'] for i in range(4)}}), 1)

    def test_reproducible_split_independent_of_input_order(self):
        rows = [fixture(i) for i in range(20)]
        first, a, _ = build_splits(rows, seed=7)
        second, b, _ = build_splits(list(reversed(rows)), seed=7)
        self.assertEqual(first, second); self.assertEqual(a, b)
        self.assertEqual([a['summary'][s]['records'] for s in ('train', 'validation', 'test')], [16, 2, 2])

    def test_cross_split_lineage_rejected(self):
        left, right = fixture(), fixture(1)
        left['split'] = 'train'; right['split'] = 'test'
        right['source']['deck_family_id'] = left['source']['deck_family_id']
        self.assertTrue(validate_rows([left, right]))

    def test_legacy_contamination_isolated(self):
        legacy, files = legacy_inputs()
        self.assertEqual(sum(f['cases'] for f in files), 64)
        row = fixture(); row['input'] = copy.deepcopy(legacy[0]['input'])
        # Use its first actual merged request as a one-segment fixture.
        text = row['input']['utterances'][0]['text']
        row['input']['utterances'] = [{'number': 1, 'text': text}]
        row['snapshot']['segments'][0]['text'] = text
        row['target']['compact'] = {k: {'s': -1, 'e': []} for k in row['input']['keypoints']}
        row['mapping']['keypoint_ids'] = {k: 'original-'+k for k in row['input']['keypoints']}
        row['annotation']['point_details'] = {k: {'relation': 'not_mentioned', 'rationale': 'fixture'} for k in row['input']['keypoints']}
        result, _, _ = build_splits([row])
        self.assertEqual(result[0]['split'], 'legacy_regression')
        row['split'] = 'train'
        with self.assertRaises(DatasetError): export_sft([row])

    def test_minimal_pair_kept_and_grouped(self):
        a, b = fixture(), fixture(1)
        text = '이 시스템은 최대 50분 동안 기기 안에서 안전하게 동작합니다.'
        for row, t in [(a, text), (b, text.replace('50', '30'))]:
            row['input']['keypoints']['1'] = text
            row['input']['utterances'][0]['text'] = t; row['snapshot']['segments'][0]['text'] = t
        b['target']['compact']['1']['s'] = 0
        out, _, report = build_splits([a, b])
        self.assertEqual(len(out), 2); self.assertEqual(out[0]['split'], out[1]['split'])
        self.assertEqual(report['pairs'][0]['kind'], 'near_candidate')

    def test_sft_no_metadata_and_exact_current_prompt(self):
        row = fixture(); row['split'] = 'train'
        output = export_sft([row])[0]
        self.assertEqual([m['role'] for m in output['messages']], ['system', 'user', 'assistant'])
        self.assertEqual(output['messages'][0]['content'], SYSTEM_PROMPT)
        self.assertEqual(json.loads(output['messages'][1]['content']), row['input'])
        self.assertEqual(json.loads(output['messages'][2]['content']), row['target']['compact'])
        self.assertNotIn('annotation', output['messages'][1]['content'])
        with self.assertRaises(DatasetError): export_sft([row], split='test')

    def test_eval_never_derives_final_state_from_compact(self):
        row = fixture(); row['split'] = 'test'
        single = export_eval([row])
        self.assertIn('requests', single); self.assertNotIn('cases', single)
        with self.assertRaises(DatasetError): export_eval([row], mode='session')
        row['reference'] = {'scope': 'legacy_evaluator_replay', 'review_status': 'adjudicated', 'phase': 'ongoing',
                            'points': {'1': {'expected_status': 'unconfirmed', 'expected_missing': False}}}
        session = export_eval([row], mode='session')
        self.assertEqual(session['cases'][0]['points'][0]['expected_status'], 'unconfirmed')
        self.assertEqual(validate_cases(session), session['cases'])
        row['reference']['points']['1']['expected_status'] = 'explained'
        replay = export_eval([row], mode='session')
        self.assertEqual(evaluate_cases(replay, PhraseCoach())['accuracy'], 1)

    def test_manifest_tampering_fails(self):
        rows, manifest, _ = build_splits([fixture(i) for i in range(10)])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for split in ('train', 'validation', 'test', 'legacy_regression', 'quarantine'):
                write_jsonl(root/(split+'.jsonl'), [r for r in rows if r['split'] == split])
            write_json(root/'manifest.json', manifest)
            loaded, _ = load_release(root)
            self.assertEqual(len(loaded), 10)
            p = root/'train.jsonl'; changed = load_jsonl(p); changed[0]['annotation']['confidence'] = 'medium'; write_jsonl(p, changed)
            with self.assertRaises(DatasetError): load_release(root)

    def test_cli_end_to_end_and_no_partial_invalid_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); input_path = root/'input.jsonl'; release = root/'release'
            write_jsonl(input_path, [fixture(i) for i in range(10)])
            def run(script, *args):
                return subprocess.run([sys.executable, str(ROOT/'scripts/datasets'/script), *map(str, args)], capture_output=True, text=True, encoding='utf-8')
            proc = run('build_splits.py', input_path, '--output-dir', release)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            proc = run('export_sft.py', release, '--output', root/'sft.jsonl')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((root/'sft.jsonl.manifest.json').exists())
            proc = run('export_eval.py', release, '--output', root/'eval.json')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            proc = run('export_eval.py', release, '--mode', 'session', '--output', root/'should-not-exist.json')
            self.assertNotEqual(proc.returncode, 0); self.assertFalse((root/'should-not-exist.json').exists())


if __name__ == '__main__':
    unittest.main()
