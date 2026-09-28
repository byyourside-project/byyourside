import unittest
from src.metrics import normalize_text, compute_cer

class TestCerMetrics(unittest.TestCase):
    def test_normalize_text(self):
        raw = "  조금만, 생각을 하면서! 살면?   "
        norm = normalize_text(raw, remove_punct=True)
        self.assertEqual(norm, "조금만 생각을 하면서 살면")

    def test_cer_identical(self):
        res = compute_cer("조금만 생각을 하면서 살면", "조금만 생각을 하면서 살면")
        self.assertEqual(res["cer"], 0.0)
        self.assertEqual(res["distance"], 0)

    def test_cer_substitution(self):
        # 1 substitution in 4 chars: 1/4 = 0.25
        res = compute_cer("가나다라", "가나마라")
        self.assertEqual(res["distance"], 1)
        self.assertEqual(res["substitutions"], 1)
        self.assertEqual(res["cer"], 0.25)

    def test_cer_with_space_and_punct(self):
        ref = "15% 증가했습니다."
        hyp = "십오 퍼센트 증가했습니다"
        res = compute_cer(ref, hyp, remove_punct=True)
        self.assertGreater(res["cer"], 0.0)

if __name__ == "__main__":
    unittest.main()
