import json
import re
import unicodedata


def _normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def score_decision(expected, answer, tags):
    if not isinstance(expected, dict) or not isinstance(expected.get("decision"), str):
        return 0.0
    try:
        result = json.loads(answer)
    except (TypeError, ValueError):
        return 0.0
    if not isinstance(result, dict) or not isinstance(result.get("output"), dict):
        return 0.0
    actual = result["output"].get("decision")
    if not isinstance(actual, str) or not actual.strip():
        return 0.0
    return 1.0 if _normalized(actual) == _normalized(expected["decision"]) else 0.0
