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
    # Use csv.reader so we correctly handle CSVs with no header row.
    # Each row is expected to have the weight and score as the last two columns.
    reader = csv.reader(io.StringIO(csv_text))
    total_weight = 0.0
    weighted_sum = 0.0

    for row in reader:
        if not row:
            continue

        # If a header row is present, its last two values won't parse as floats
        # so attempting to convert them will raise and we will skip that row.
        if len(row) < 2:
            continue

        weight_str = row[-2]
        score_str = row[-1]

        try:
            weight = float(weight_str)
            score = float(score_str)
        except Exception:
            # skip header or malformed rows
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


test_csv = """qualification,weight,score\nProduct management experience 5+ years including 3+ years B2B,10,10\nTechnical engineering experience 3+ years,10,8\nProduct lifecycle expertise ideation to deployment monitoring,9,9\nData driven decision making leveraging analytics,9,9\nCross functional collaboration with R&D and GTM,8,9\nUser experience design and journey optimization,8,8\nSuccess metrics definition and measurement,8,9\nStrong communication skills across roles,9,9\nCritical thinking problem identification and opportunity spotting,8,8\nExperience with Salesforce HubSpot or cloud marketplaces AWS Azure GCP,7,6\nBachelor's degree in Computer Science Software Engineering or related field,6,8"""
print(csv_to_score(test_csv))  