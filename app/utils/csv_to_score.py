import csv
import io
from typing import Optional

def csv_to_score(csv_text: str) -> int:
    """
    Parse a CSV string with columns including 'Weight' and 'Score' and return
    the weighted average scaled to 0-100 as an integer.

    Formula: result = round(10 * (sum(score * weight) / sum(weight)))

    Returns 0 if there are no valid weighted rows.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    total_weight = 0.0
    weighted_sum = 0.0

    for row in reader:
        # Accept header keys with possible surrounding whitespace
        weight_str = row.get("Weight") or row.get(" weight") or row.get("Weight ")
        score_str = row.get("Score") or row.get(" score") or row.get("Score ")

        if weight_str is None or score_str is None:
            # Try to fallback by index if DictReader didn't match headers
            # (handle malformed headers or different casing)
            try:
                # Re-parse row values in order: Qualification,Weight,Score
                values = list(row.values())
                weight_str = values[1]
                score_str = values[2]
            except Exception:
                continue

        try:
            weight = float(weight_str)
            score = float(score_str)
        except Exception:
            continue

        if weight <= 0:
            continue

        weighted_sum += score * weight
        total_weight += weight

    if total_weight == 0:
        return 0

    # average score in 0-10 then scale to 0-100
    scaled = 10.0 * (weighted_sum / total_weight)
    return int(round(scaled))
