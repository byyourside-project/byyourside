"""Necessary evidence checks for a model's positive content judgment.

These checks never establish that a claim was explained. They only compare
explicit supported quantities or detect a few definite unfinished endings.
Unsupported measurements and claims without supported Arabic quantities return
None, leaving their semantic judgment to the coach.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re
from difflib import SequenceMatcher


_SINO_DIGITS = {char: value for value, char in enumerate("영일이삼사오육칠팔구")}
_SINO_DIGITS["공"] = 0
_SMALL_UNITS = {"십": 10, "백": 100, "천": 1000}
_LARGE_UNITS = {"만": 10000, "억": 100000000, "조": 1000000000000}
_NATIVE_ONES = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3,
                "네": 4, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7,
                "여덟": 8, "아홉": 9}
_NATIVE_TENS = {"열": 10, "스물": 20, "스무": 20, "서른": 30, "마흔": 40,
                "쉰": 50, "예순": 60, "일흔": 70, "여든": 80, "아흔": 90}
_TIME_FACTORS = {"초": Decimal(1), "분": Decimal(60), "시간": Decimal(3600)}
_TIME_ORDER = {"시간": 3, "분": 2, "초": 1}
_NUMERAL_CHARS = "".join(sorted(set("".join(_SINO_DIGITS) + "".join(_SMALL_UNITS) +
                                      "".join(_LARGE_UNITS) + "".join(_NATIVE_ONES) +
                                      "".join(_NATIVE_TENS) + "점")))
_ARABIC = r"[+\-−]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)"
_ARABIC_START = r"(?<![0-9A-Za-z.,+\-−])"
_KOREAN = rf"(?:마이너스\s*|플러스\s*)?[{_NUMERAL_CHARS}](?:[{_NUMERAL_CHARS}\s]*[{_NUMERAL_CHARS}])?"
_KOREAN_START = r"(?:(?<![가-힣A-Za-z0-9.,+\-−])|(?<=시간)|(?<=분)|(?<=초))"
_QUANTITY = re.compile(rf"(?P<number>{_ARABIC_START}{_ARABIC}|{_KOREAN_START}{_KOREAN})\s*(?P<unit>퍼센트|시간|초|분|원|개|명|회|%)")
_PARTICLE = re.compile(r"(?:입니다|이었다|이었|이라|이고|이며|이면|이니|이다|인|예요|였|죠|네요|뿐|라서|라고|라면|랍니다|요|은|는|이|가|을|를|의|에|로|으로|보다|까지|부터|마다|당|도|만|씩|나|와|과|밖에|내|동안|간|정도|가량|쯤|이하|이상|미만|넘게|중)")
_AMBIGUOUS_ATTACHED = {"원": set(_SINO_DIGITS), "명": set("일이사오구"),
                       "회": {"사"}, "분": {"구"}}


@dataclass(frozen=True)
class _Quantity:
    value: Decimal
    dimension: str
    unit: str
    start: int
    end: int


def _small_sino(text):
    if not text:
        return 0
    total, digit, previous_unit = 0, None, 10000
    for char in text:
        if char in _SINO_DIGITS:
            if digit is not None:
                return None  # Digit-by-digit identifiers are not cardinal amounts.
            digit = _SINO_DIGITS[char]
        elif char in _SMALL_UNITS:
            unit = _SMALL_UNITS[char]
            if unit >= previous_unit or digit == 0:
                return None
            total += (1 if digit is None else digit) * unit
            digit, previous_unit = None, unit
        else:
            return None
    return total + (digit if digit is not None else 0)


def _sino_integer(text):
    if not text:
        return None
    total, section, previous_unit = 0, "", float("inf")
    for char in text:
        if char in _LARGE_UNITS:
            unit = _LARGE_UNITS[char]
            amount = _small_sino(section) if section else 1
            if unit >= previous_unit or amount is None or amount == 0:
                return None
            total += amount * unit
            section, previous_unit = "", unit
        else:
            section += char
    amount = _small_sino(section)
    return None if amount is None else total + amount


def _native_integer(text):
    if text in _NATIVE_ONES:
        return _NATIVE_ONES[text]
    if text in _NATIVE_TENS:
        return _NATIVE_TENS[text]
    for tens, amount in _NATIVE_TENS.items():
        if tens != "스무" and text.startswith(tens) and text[len(tens):] in _NATIVE_ONES:
            return amount + _NATIVE_ONES[text[len(tens):]]
    return None


def _korean_integer(text):
    value = _sino_integer(text)
    if value is not None:
        return value
    value = _native_integer(text)
    if value is not None:
        return value
    # Counts commonly combine Sino hundreds and native tens/ones (백스물세 개).
    for split in range(1, len(text)):
        if text[split - 1] not in ("백", "천", "만", "억", "조"):
            continue
        high, low = _sino_integer(text[:split]), _native_integer(text[split:])
        if high is not None and low is not None:
            return high + low
    return None


def _number(text):
    compact = re.sub(r"\s+", "", text)
    if re.fullmatch(_ARABIC, compact):
        try:
            return Decimal(compact.replace(",", "").replace("−", "-"))
        except InvalidOperation:
            return None
    sign = 1
    if compact.startswith("마이너스"):
        sign, compact = -1, compact[len("마이너스"):]
    elif compact.startswith("플러스"):
        compact = compact[len("플러스"):]
    if "점" in compact:
        if compact.count("점") != 1:
            return None
        whole, fraction = compact.split("점")
        whole = _korean_integer(whole) if whole else 0
        if whole is None or not fraction or any(c not in _SINO_DIGITS for c in fraction):
            return None
        value = Decimal(str(whole) + "." + "".join(str(_SINO_DIGITS[c]) for c in fraction))
        return value.copy_negate() if sign < 0 else value
    value = _korean_integer(compact)
    return None if value is None else Decimal(sign * value)


def _add_exact(left, right):
    # Decimal's default 28-digit context must not merge different long amounts.
    fractional = max(0, -left.as_tuple().exponent, -right.as_tuple().exponent)
    integral = max(1, left.adjusted() + 1, right.adjusted() + 1)
    with localcontext() as context:
        context.prec = max(28, integral + fractional + 2)
        return left + right


def _seconds_exact(value, unit):
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 4)
        return value * _TIME_FACTORS[unit]


def _unit_ends_here(text, end):
    if end == len(text):
        return True
    following = text[end:]
    first = following[0]
    if first.isspace() or not (first.isalnum() or first == "_"):
        return True
    # Adjacent time components, e.g. 1분30초, are parsed separately then added.
    next_quantity = _QUANTITY.match(text, end)
    compact_time = (next_quantity is not None and next_quantity["unit"] in _TIME_FACTORS and
                    _number(next_quantity["number"]) is not None)
    return first.isdigit() or compact_time or bool(_PARTICLE.match(following))


def _quantities(text, *, korean):
    found = []
    for match in _QUANTITY.finditer(text):
        raw_number, unit = match["number"], match["unit"]
        arabic = bool(re.fullmatch(_ARABIC, raw_number))
        if not arabic and not korean:
            continue
        start, end = match.span()
        if start:
            previous = text[start - 1]
            if previous in "0123456789.,+-−" or previous.isascii() and previous.isalpha():
                continue
            if not arabic and "가" <= previous <= "힣":
                # Permit the next component of a compact Korean duration only.
                if not (found and found[-1].dimension == "time" and found[-1].end == start):
                    continue
        value = _number(raw_number)
        if value is None:
            continue
        # Some attached Sino amounts are ordinary nouns: 구원, 사원, 사명,
        # 사회, 구분, etc. Require separation for these ambiguous combinations;
        # native 세 명/네 명 and unambiguous larger amounts remain supported.
        if (not arabic and raw_number in _AMBIGUOUS_ATTACHED.get(unit, ()) and
                not text[match.end("number"):match.start("unit")]):
            continue
        if unit in _TIME_FACTORS:
            half = re.match(r"\s*반", text[end:])
            if half and _unit_ends_here(text, end + half.end()):
                value = _add_exact(value, Decimal("0.5") if value >= 0 else Decimal("-0.5"))
                end += half.end()
        if not _unit_ends_here(text, end):
            continue
        if unit in _TIME_FACTORS:
            dimension, value = "time", _seconds_exact(value, unit)
        else:
            dimension = "percent" if unit in ("%", "퍼센트") else unit
        quantity = _Quantity(value, dimension, unit, start, end)
        if (found and dimension == "time" and found[-1].dimension == "time" and
                _TIME_ORDER[found[-1].unit] > _TIME_ORDER[unit] and
                not text[found[-1].end:start].strip()):
            previous = found.pop()
            quantity = _Quantity(_add_exact(previous.value, value), "time", unit, previous.start, end)
        found.append(quantity)
    return found


def quantity_evidence_supported(expected_text: str, evidence_text: str) -> bool | None:
    """Check supported explicit quantities without approving a semantic claim.

    Expected quantities use Arabic numerals. Evidence may use Arabic or Korean
    cardinal numerals. Supported units are 초/분/시간, 원, %/퍼센트, 개, 명, 회.
    Time is normalized to seconds, including adjacent compound durations. Other
    units remain distinct; e.g. 3개 cannot support 3명. A True result means only
    that every expected quantity occurs in the evidence, and does not resolve
    negation, relationships, approximation, or which subject owns a number.
    """
    if not isinstance(expected_text, str) or not isinstance(evidence_text, str):
        raise TypeError("Expected text and evidence text must be strings")
    expected = _quantities(expected_text, korean=False)
    if not expected:
        return None
    evidence = {(q.dimension, q.value) for q in _quantities(evidence_text, korean=True)}
    return all((q.dimension, q.value) in evidence for q in expected)


def quantity_evidence_present(expected_text: str, evidence_text: str) -> bool | None:
    """Distinguish missing evidence from a spoken but incompatible quantity.

    This never approves a claim. If a numbered claim cites speech without any
    supported quantity, that citation cannot retract an earlier valid claim.
    """
    expected = _quantities(expected_text, korean=False)
    if not expected:
        return None
    return bool(_quantities(evidence_text, korean=True))


def evidence_matches_other_claim(expected_text, evidence_texts, other_texts, aliases=()):
    """Detect citations that literally describe distinct neighboring points.

    Only reject when every cited utterance nearly reproduces another point and
    is lexically far from this point (including aliases). Ordinary paraphrases,
    shared topic statements and unknown evidence remain the model's decision.
    Whitespace normalization here routes/rejects citations, never confirms them.
    """
    def body(text):
        text = re.sub(r"\s+", "", text.casefold())
        text = re.sub(r"[.!?。！？]+$", "", text)
        return re.sub(r"(?:합니다|됩니다|입니다|습니다)$", "", text)

    targets = [body(text) for text in (expected_text, *aliases)]
    others = [body(text) for text in other_texts]
    if not evidence_texts or not others:
        return False
    for text in evidence_texts:
        cited = body(text)
        if not cited or max((SequenceMatcher(None, target, cited).ratio() for target in targets), default=0) >= .4:
            return False
        if max((SequenceMatcher(None, other, cited).ratio() for other in others), default=0) < .94:
            return False
    return True


def unfinished_tail(text: str) -> bool:
    """Detect only definite continuation endings; ordinary nouns stay valid.

    STT punctuation cannot make 보내지 않고 a complete predicate. Avoid a broad
    -고 rule because complete nouns such as 창고 and 최고 share that ending.
    """
    if not isinstance(text, str):
        raise TypeError("Text must be a string")
    tail = text.rstrip().rstrip(".!?。！？…,:;\"'”’)]}").rstrip()
    if not tail:
        return False
    compact = re.sub(r"\s+", "", tail)
    if compact.endswith(("지않고", "하지만", "에서", "기때문에", "기위해")):
        return True
    return bool(re.search(r"(?:^|\s)(?:그리고|또는|및|최대|최소)$", tail))
