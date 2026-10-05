import unittest

from src.semantic_guards import (quantity_evidence_supported, quantity_evidence_present,
                                 evidence_matches_other_claim, unfinished_tail)


class QuantityGuardTests(unittest.TestCase):
    def test_supported_equivalent_quantities(self):
        cases = [
            ("최대 4초입니다.", "최대 사 초입니다."),
            ("4초 이내", "사초 이내"),
            ("2초", "두 초"),
            ("4초", "네 초"),
            ("60초", "1분"),
            ("60초", "일 분"),
            ("120초", "두 분"),
            ("3600초", "한 시간"),
            ("1시간", "60분"),
            ("200원", "이백원"),
            ("1000원", "천원"),
            ("100000원", "십만원"),
            ("100000000원", "일억원"),
            ("1,234원", "천이백삼십사 원"),
            ("3명", "세 명"),
            ("11개", "열한 개"),
            ("22개", "스물두개"),
            ("23명", "스물 세 명"),
            ("99회", "아흔아홉 회"),
            ("123개", "백 스물세 개"),
            ("20%", "이십 퍼센트"),
            ("5퍼센트", "오 %"),
            ("0원", "영 원"),
        ]
        for expected, evidence in cases:
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), True)

    def test_numeric_mismatch_or_absence_never_supports_positive_judgment(self):
        cases = [
            ("4초", "삼 초"),
            ("4초", "세 초"),
            ("4초", "3초"),
            ("4초", "음성을 빠르게 인식합니다."),
            ("200원", "이백일 원"),
            ("3명", "세 개"),
            ("3개", "세 회"),
            ("60초", "두 분"),
            ("10%", "십 원"),
            ("4초와 200원", "사 초와 이백일 원"),
            ("4초와 200원", "사 초만 설명합니다."),
        ]
        for expected, evidence in cases:
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), False)

    def test_multiple_supported_dimensions_all_need_evidence(self):
        expected = "4초 동안 3명이 각각 2회 수행하고 비용은 1,000원, 감소율은 20%입니다."
        evidence = "삼 명이 두 회씩 수행합니다. 소요 시간은 사 초이고 천 원을 씁니다. 이십 퍼센트 줄었습니다."
        self.assertIs(quantity_evidence_supported(expected, evidence), True)
        self.assertIs(quantity_evidence_supported(expected, evidence.replace("두 회", "세 회")), False)
        self.assertIs(quantity_evidence_supported(expected, evidence.replace("이십 퍼센트", "")), False)

    def test_natural_copulas_and_particles_preserve_matching_amounts(self):
        for expected, evidence in (("최대 4초입니다", "최대 사 초예요."), ("4초", "사초였어요."),
                                   ("3명", "세 명뿐이에요."), ("20%", "이십 퍼센트죠."),
                                   ("4초", "사초네요."), ("4초", "사 초라서 충분해요."),
                                   ("4초", "사초요."), ("200원", "이백원이었죠."),
                                   ("4명", "네명예요."), ("4명", "사 명뿐이에요.")):
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), True)

    def test_decimals_use_exact_values_and_valid_thousands_grouping(self):
        cases = [
            ("0.3초", "영 점 삼 초", True),
            ("3.5초", "삼점오초", True),
            ("3.50초", "3.5초", True),
            (".5초", "공점오초", True),
            ("1,234.50원", "천이백삼십사점오 원", True),
            ("1,234,567원", "1234567원", True),
            ("3.5초", "삼점육 초", False),
            ("5초", "3,5초", False),
            ("20원", "1,20원", False),
            ("345원", "12,345원", False),
            ("5초", "3.5초", False),
            ("6초", "3.5.6초", False),
            ("3초", "1e3초", False),
            ("4초", "0x4초", False),
        ]
        for expected, evidence, supported in cases:
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), supported)

    def test_adjacent_compound_duration_is_one_quantity(self):
        for expected, evidence in (("90초", "1분30초"), ("90초", "일분삼십초"),
                                   ("90초", "한 분 삼십 초"), ("5400초", "한시간삼십분"),
                                   ("1시간 2분 3초", "3723초"),
                                   ("3723초", "한시간이분삼초")):
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), True)
        self.assertIs(quantity_evidence_supported("60초", "1분30초"), False)
        self.assertIs(quantity_evidence_supported("90초", "1분과 30초"), False)
        self.assertIs(quantity_evidence_supported("60초와 30초", "1분, 30초"), True)

    def test_half_duration_is_not_mistaken_for_its_integer_prefix(self):
        self.assertIs(quantity_evidence_supported("90초", "한 분 반"), True)
        self.assertIs(quantity_evidence_supported("60초", "한 분 반"), False)
        self.assertIs(quantity_evidence_supported("1시간 반", "90분"), True)
        self.assertIs(quantity_evidence_supported("4초", "4초 반복"), True)

    def test_signed_values_are_not_silently_made_positive(self):
        for expected, evidence in (("-3초", "마이너스 삼 초"), ("−3초", "-3초"), ("+3초", "플러스 삼 초")):
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), True)
        self.assertIs(quantity_evidence_supported("3초", "-3초"), False)
        self.assertIs(quantity_evidence_supported("3초", "마이너스삼초"), False)

    def test_long_quantities_are_not_rounded_to_false_equivalence(self):
        minutes = 10 ** 35 + 1
        self.assertIs(quantity_evidence_supported(f"{minutes * 60}초", f"{minutes}분"), True)
        self.assertIs(quantity_evidence_supported(f"{minutes * 60}초", f"{minutes + 1}분"), False)
        fraction = "1" * 40
        self.assertIs(quantity_evidence_supported("0." + fraction + "초", "영점" + "일" * 40 + "초"), True)
        self.assertIs(quantity_evidence_supported("0." + fraction + "초", "영점" + "일" * 39 + "이초"), False)

    def test_no_supported_expected_quantity_returns_no_guard(self):
        for expected in ("기기에서 음성을 인식합니다.", "버전 2.0을 사용합니다.", "4개월", "3분기",
                         "5kg", "30ms", "3.5MB", "2026년", "사 초", "1,20원", "3.5.6초", "20%p"):
            with self.subTest(expected=expected):
                self.assertIsNone(quantity_evidence_supported(expected, "아무 숫자도 없습니다."))
        self.assertIs(quantity_evidence_supported("4초와 5kg", "사 초"), True)

    def test_korean_noun_suffix_is_not_a_number(self):
        for expected, evidence in (("9원", "사람을 구원"), ("4원", "새로운 사원"),
                                   ("1원", "팀의 일원"), ("0원", "영원"),
                                   ("9초", "연구 초반입니다."), ("2회", "회의를 마칩니다.")):
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), False)
        self.assertIs(quantity_evidence_supported("9원", "구 원"), True)

    def test_new_copulas_do_not_turn_ordinary_nouns_into_amounts(self):
        for expected, evidence in (("9원", "사람의 구원예요."), ("4원", "새 사원였어요."),
                                   ("1원", "팀의 일원뿐이에요."), ("0원", "영원이네요."),
                                   ("4명", "회사의 사명예요."), ("5명", "오명뿐이에요."),
                                   ("2명", "이명이었어요."), ("9명", "구명이죠."),
                                   ("4회", "새로운 사회예요."), ("9분", "명확한 구분이죠."),
                                   ("3개", "세 개념예요."), ("4원", "사 원칙이죠."),
                                   ("3명", "삼 명령이었어요.")):
            with self.subTest(expected=expected, evidence=evidence):
                self.assertIs(quantity_evidence_supported(expected, evidence), False)

    def test_invalid_korean_cardinal_structure_is_not_guessed(self):
        for evidence in ("삼사 초", "십십 초", "영십 초", "일이삼 원", "일만만 원", "삼점십 초"):
            with self.subTest(evidence=evidence):
                self.assertIs(quantity_evidence_supported("4초와 123원", evidence), False)


class IndependentEvidenceTests(unittest.TestCase):
    def test_missing_quantity_citation_is_distinct_from_spoken_wrong_quantity(self):
        self.assertIs(quantity_evidence_present("최대 4초 구간입니다.", "음성 인식은 로컬에서 실행합니다."), False)
        for evidence in ("최대 삼 초입니다.", "최대 사 분입니다.", "4회입니다."):
            with self.subTest(evidence=evidence):
                self.assertIs(quantity_evidence_present("최대 4초입니다.", evidence), True)
        self.assertIsNone(quantity_evidence_present("실행합니다.", "4초입니다."))
        self.assertIsNone(quantity_evidence_present("4개월입니다.", "4초입니다."))

    def test_literal_other_sentence_cannot_support_unrelated_point(self):
        source = "발화 를 최대 4초 구간 으로 분할 합니다."
        self.assertTrue(evidence_matches_other_claim(
            "불확실한 내용은 판단을 보류합니다.", [source],
            ["발화를 최대 4초 구간으로 분할합니다."]))
        self.assertTrue(evidence_matches_other_claim(
            "실제 발표 음성으로 정확도와 지연을 검증합니다.",
            ["불 확 실한 내 용은 판단 을 보류 합니다."],
            ["불확실한 내용은 판단을 보류합니다."]))

    def test_paraphrase_alias_and_shared_topic_remain_semantic_decisions(self):
        self.assertFalse(evidence_matches_other_claim(
            "음성 인식은 로컬에서 실행합니다.",
            ["외부 서버에 녹음을 보낼 필요 없이 기기 안에서 음성을 글로 바꿉니다."],
            ["발화를 최대 4초 구간으로 분할합니다."]))
        self.assertFalse(evidence_matches_other_claim(
            "시간 관리와 핵심 내용 전달을 지원합니다.", ["발표자의 시간과 핵심 내용 설명을 돕습니다."],
            ["발표자의 시간과 핵심 내용 설명을 돕습니다."],
            ["발표자의 시간과 핵심 내용 설명을 돕습니다."]))
        self.assertFalse(evidence_matches_other_claim(
            "음성 인식은 로컬에서 실행합니다.", ["음성 인식은 외부 서버에서 실행합니다."],
            ["음성 인식은 외부 서버에서 실행합니다."]))

    def test_a_mixed_valid_citation_or_unknown_text_cannot_be_rejected_by_literal_guard(self):
        expected = "불확실한 내용은 판단을 보류합니다."
        other = "발화를 최대 4초 구간으로 분할합니다."
        self.assertFalse(evidence_matches_other_claim(expected, [other, expected], [other]))
        self.assertFalse(evidence_matches_other_claim(expected, ["이것은 다른 표현입니다."], [other]))
        self.assertFalse(evidence_matches_other_claim(expected, [], [other]))
        self.assertFalse(evidence_matches_other_claim(expected, [other], []))


class UnfinishedTailTests(unittest.TestCase):
    def test_definite_continuations_ignore_stt_punctuation(self):
        for text in ("음성 인식은 외부로 보내지 않고", "음성을 외부로 보내지않고.",
                     "최대한 빠르게 동작하지만!", "다음 항목은 그리고", "다음 항목 또는…",
                     "이 속도는 최대", "필요 인원은 최소", "음성을 기기에서", "음성을 기기에서.",
                     "성능이 충분하기 때문에", "발표를 돕기 위해", "필수 기능 및", "그리고"):
            with self.subTest(text=text):
                self.assertTrue(unfinished_tail(text))

    def test_complete_predicates_and_ordinary_nouns_are_not_cut_off(self):
        for text in ("창고", "최고", "재고", "창고.", "최고입니다.", "외부로 보내지 않고 처리합니다.",
                     "하지만 정상적으로 동작합니다.", "그리고 발표를 마칩니다.", "최대 4초입니다.",
                     "최소 3명이 필요합니다.", "이 모델은 세계최대", "기기에서 처리합니다.",
                     "예시는 '그리고'", "인식합니다", "", "   "):
            with self.subTest(text=text):
                self.assertFalse(unfinished_tail(text))


if __name__ == "__main__":
    unittest.main()
