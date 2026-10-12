import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from src.maum_contract import encode, decode, validate_target
from scripts.export_maum_dataset import export, approved, import_workbook, validate

ROOT = Path(__file__).resolve().parents[1]


class MaumContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads((ROOT / 'docs/data/maumai/seed_dataset.json').read_text(encoding='utf-8'))

    def test_all_seed_records_roundtrip_and_references(self):
        validate(self.dataset)
        for record in self.dataset['records']:
            with self.subTest(sample=record['sample_id']):
                self.assertEqual(decode(encode(record['target']), record['input']), record['target'])

    def test_model_text_never_executes_and_rejects_malformed_calls(self):
        bad = [
            '<maum_2>(state=__import__("os").getcwd(), text="")<maum_end>',
            '<maum_2>(state="fast", text="", text="again")<maum_end>',
            '<maum_2>(**{})<maum_end>', '<maum_2>("fast", "text")<maum_end>',
            '<maum_2>(state="fast", text="")<jeju_end>',
            '<maum_5>(state="fast", text="")<maum_end>',
            '<maum_2>(state="fast", text="")<maum_end> extra',
            '<maum_1>(states={"P1":{"s":1,"s":0,"e":[1]}})<maum_end>',
        ]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError): decode(text)

    def test_unknown_evidence_ids_rejected(self):
        record = copy.deepcopy(next(item for item in self.dataset['records'] if item['function_id'] == 'F04'))
        record['target']['arguments']['evidence_ids'] = ['nonexistent']
        with self.assertRaises(ValueError): validate_target(record['target'], record['input'])

    def test_alert_policy_and_wrong_direction_rejected(self):
        target = {'function': 'display_pace_hint', 'arguments': {'state': 'fast', 'text': '조금 천천히'}}
        for context in [{'pace_state': 'fast', 'alert_allowed': False}, {'pace_state': 'slow', 'alert_allowed': True}]:
            with self.assertRaises(ValueError): validate_target(target, context)

    def test_explained_requires_evidence_and_integer_status(self):
        for value in [{'s': 1, 'e': []}, {'s': True, 'e': [1]}, {'s': 1, 'e': [True]}]:
            with self.assertRaises(ValueError): validate_target({'function': 'update_keypoints', 'arguments': {'states': {'P1': value}}})

    def test_group_leakage_and_duplicate_input_rejected(self):
        changed = copy.deepcopy(self.dataset)
        changed['records'][1]['split'] = 'test'
        with self.assertRaises(ValueError): validate(changed)
        changed = copy.deepcopy(self.dataset)
        changed['records'][1]['input'] = changed['records'][0]['input']
        with self.assertRaises(ValueError): validate(changed)

    def test_no_training_export_without_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'data'
            with self.assertRaises(ValueError): export(self.dataset, output)
            self.assertFalse(output.exists())

    def test_preview_is_marked_ineligible_even_for_approved_rows(self):
        changed = copy.deepcopy(self.dataset)
        changed['records'][0].update(review_status='approved', reviewer_id='tester', reviewed_on='2026-01-01', adoption='use')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'data'
            result = export(changed, output, preview=True)
            self.assertEqual(result['counts'], {'train': 48, 'validation': 14, 'test': 18})
            self.assertFalse((output / 'train.jsonl').exists())
            items = [json.loads(line) for line in (output / 'preview_train.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all(not item['training_eligible'] for item in items))

    def test_reviewed_export_keeps_test_out_of_training(self):
        changed = copy.deepcopy(self.dataset)
        for record in changed['records']:
            record.update(review_status='approved', reviewer_id='tester', reviewed_on='2026-01-01', adoption='use')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'data'
            export(changed, output)
            rows = [json.loads(line) for line in (output / 'test.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all(not row['training_eligible'] and row['split'] == 'test' for row in rows))
            with self.assertRaises(FileExistsError): export(changed, output)

    def test_future_date_or_missing_reviewer_never_approved(self):
        record = {'review_status': 'approved', 'adoption': 'use', 'reviewed_on': '2999-01-01', 'reviewer_id': 'test'}
        self.assertFalse(approved(record))
        record.update(reviewed_on='2026-01-01', reviewer_id='  ')
        self.assertFalse(approved(record))

    @unittest.skipUnless(importlib.util.find_spec('openpyxl'), 'Excel reader is an optional data dependency')
    def test_existing_workbook_import_matches_all_seed_targets(self):
        result = import_workbook(ROOT / 'outputs/week07_maumai/곁_마음AI_학습데이터시트_v1.xlsx', self.dataset)
        validate(result)
        self.assertEqual(len(result['records']), 80)
        self.assertEqual([r['target'] for r in result['records']], [r['target'] for r in self.dataset['records']])
        self.assertFalse(any(approved(record) for record in result['records']))


if __name__ == '__main__': unittest.main()
