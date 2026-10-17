import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_photo_training_data import SOURCE, completion, export


class PhotoTrainingDataTest(unittest.TestCase):
    def test_every_draft_label_exports_to_one_function_call(self):
        rows = json.loads(SOURCE.read_text())
        self.assertEqual(len(rows), 1792)
        self.assertEqual(len({row["utterance"] for row in rows}), len(rows))
        for row in rows:
            answer = completion(row)
            self.assertRegex(answer, r"^<maum_[0-6]>\([^()]*\)<maum_end>$")

    def test_export_preserves_split_and_answer(self):
        rows = json.loads(SOURCE.read_text())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            counts = export(destination=destination)
            self.assertEqual(counts, {"train": 1280, "eval": 256, "test": 256})
            for split, filename in (("train", "train.jsonl"), ("eval", "valid.jsonl"), ("test", "test.jsonl")):
                expected = [row for row in rows if row["split"] == split]
                actual = [json.loads(line) for line in (destination / filename).read_text().splitlines()]
                self.assertEqual([item["completion"] for item in actual], [completion(row) for row in expected])
                self.assertTrue(all(row["utterance"] in item["prompt"] for row, item in zip(expected, actual)))
            tokens = json.loads((destination / "special_tokens.json").read_text())["additional_special_tokens"]
            self.assertEqual(tokens, [*(f"<maum_{i}>" for i in range(7)), "<maum_end>"])


if __name__ == "__main__":
    unittest.main()
