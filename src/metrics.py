import re
import unicodedata
import resource
from typing import List, Dict, Any, Tuple
import numpy as np

def normalize_text(text: str, remove_punct: bool = True) -> str:
    """
    NFC unicode normalization and formatting.
    Optionally removes punctuation for standard CER evaluation.
    """
    if not text:
        return ""
    # 1. NFC normalization
    text = unicodedata.normalize("NFC", text)
    # 2. Lowercase
    text = text.lower()
    # 3. Handle punctuation if requested
    if remove_punct:
        # Keep letters, numbers, and Korean characters (Hangul syllables, Jamo)
        # Remove common punctuation symbols
        text = re.sub(r"[^\w\s가-힣]", " ", text)
    # 4. Collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text

def levenshtein_distance(ref: str, hyp: str) -> Tuple[int, int, int, int]:
    """
    Compute Levenshtein distance at character level.
    Returns (distance, substitutions, deletions, insertions).
    """
    n, m = len(ref), len(hyp)
    # dp[i][j] = (dist, sub, dele, ins)
    # We can use standard 2D DP table
    dp = [[(0, 0, 0, 0)] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = (i, 0, i, 0)
    for j in range(1, m + 1):
        dp[0][j] = (j, 0, 0, j)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                # Substitution
                sub_d, sub_s, sub_del, sub_ins = dp[i - 1][j - 1]
                cand_sub = (sub_d + 1, sub_s + 1, sub_del, sub_ins)
                # Deletion
                del_d, del_s, del_del, del_ins = dp[i - 1][j]
                cand_del = (del_d + 1, del_s, del_del + 1, del_ins)
                # Insertion
                ins_d, ins_s, ins_del, ins_ins = dp[i][j - 1]
                cand_ins = (ins_d + 1, ins_s, ins_del, ins_ins + 1)

                dp[i][j] = min(cand_sub, cand_del, cand_ins, key=lambda x: x[0])

    return dp[n][m]

def compute_cer(reference: str, hypothesis: str, remove_punct: bool = True) -> Dict[str, Any]:
    """
    Compute Character Error Rate (CER) between reference and hypothesis.
    """
    norm_ref = normalize_text(reference, remove_punct=remove_punct)
    norm_hyp = normalize_text(hypothesis, remove_punct=remove_punct)

    ref_chars = list(norm_ref)
    hyp_chars = list(norm_hyp)

    ref_len = len(ref_chars)
    if ref_len == 0:
        dist = len(hyp_chars)
        cer = 0.0 if dist == 0 else 1.0
        return {
            "cer": cer,
            "distance": dist,
            "ref_len": 0,
            "hyp_len": len(hyp_chars),
            "substitutions": 0,
            "deletions": 0,
            "insertions": dist,
            "norm_ref": norm_ref,
            "norm_hyp": norm_hyp,
        }

    dist, sub, dele, ins = levenshtein_distance(ref_chars, hyp_chars)
    cer = dist / ref_len

    return {
        "cer": cer,
        "distance": dist,
        "ref_len": ref_len,
        "hyp_len": len(hyp_chars),
        "substitutions": sub,
        "deletions": dele,
        "insertions": ins,
        "norm_ref": norm_ref,
        "norm_hyp": norm_hyp,
    }

def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """Calculate p50, p95, p99, min, max, mean for a list of values."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0, "count": 0}
    arr = np.array(values)
    return {
        "count": len(values),
        "mean": float(np.mean(arr)),
        "min": float(np.min(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
    }

def get_process_memory_mb() -> float:
    """Get current process peak RSS memory in MB."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # On macOS, ru_maxrss is in bytes; on Linux, in kilobytes.
    # macOS Darwin check:
    import sys
    if sys.platform == "darwin":
        return usage.ru_maxrss / (1024.0 * 1024.0)
    else:
        return usage.ru_maxrss / 1024.0
