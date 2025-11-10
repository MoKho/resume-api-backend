import re
import unicodedata
from collections import Counter
from typing import Dict, Tuple, Any


def normalize_to_ascii(text: str) -> Tuple[bool, str, Dict[str, Dict[str, Any]]]:
    """Replace known non-ASCII characters with ASCII equivalents.

    Returns (found_non_ascii, fixed_text, replacements)
    - found_non_ascii: True if any non-ascii characters were found (mapped or unmapped)
    - fixed_text: the normalized ASCII-only string for characters that had mappings/transliterations;
                  characters without an ASCII equivalent are LEFT IN PLACE.
    - replacements: dict mapping original-char -> {"replacement": str|None, "count": int,
      "codepoint": "U+XXXX", "unfixable": bool}. If "replacement" is None and "unfixable" is True,
      the original character was detected but not changed.
    """

    if not text:
        return False, text, {}

    # Build explicit mapping table (char -> replacement)
    mapping = {
        # Space-like -> regular space or removed
        "\u00A0": " ",  # No-break space
        "\u1680": " ",
        "\u2000": " ",
        "\u2001": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2004": " ",
        "\u2005": " ",
        "\u2006": " ",
        "\u2007": " ",
        "\u2008": " ",
        "\u2009": " ",
        "\u200A": " ",
        "\u202F": " ",
        "\u205F": " ",
        "\u3000": " ",
        # zero-width / joiners -> remove
        "\u200B": "",
        "\u200C": "",
        "\u200D": "",
        "\u2060": "",
        "\uFEFF": "",

        # Quotes -> straight equivalents
        "\u2018": "'",
        "\u2019": "'",
        "\u201A": "'",
        "\u201B": "'",
        "\u201C": '"',
        "\u201D": '"',
        "\u201E": '"',
        "\u201F": '"',

        # Dashes/hyphens
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        # Common math and arrow symbols often produced by LLMs
        "\u00D7": "x",   # multiplication sign ×
        "\u00F7": "/",   # division sign ÷
        "\u2192": "->",  # rightwards arrow →
        "\u2190": "<-",  # leftwards arrow ←
        "\u2194": "<->", # left-right arrow ↔
        "\u21D2": "=>",  # rightwards double arrow ⇒
        "\u21D0": "<=",  # leftwards double arrow ⇐
        "\u21D4": "<=>", # left-right double arrow ⇔

        "\u2015": "-",
        "\u2212": "-",

        # Ellipsis
        "\u2026": "...",

        # Line/paragraph separators -> normalize to \n or remove soft hyphen
        "\u2028": "\n",
        "\u2029": "\n",
        "\u00AD": "",  # soft hyphen: remove

        # Bullets and dots
        "\u00B7": ".",
        "\u2022": "-",
        "\u2023": "-",
        "\u25AA": "-",
        "\u25CF": "-",
        "\u25E6": "-",
        "\u2024": ".",
        "\u2027": ".",
        "\u22C5": ".",

        # Guillemets and primes
        "\u00AB": '"',
        "\u00BB": '"',
        "\u2032": "'",
        "\u2033": '"',
        "\u00B0": "deg",
        "\u2010": "-",
        "\u2043": "-",
    }

    # Build regex to find any of the mapped characters
    pattern = re.compile("|".join(re.escape(k) for k in mapping.keys()))

    found_counter = Counter()

    def _replace_match(m: re.Match) -> str:
        ch = m.group(0)
        found_counter[ch] += 1
        return mapping.get(ch, "")

    # First pass: apply explicit mapping
    text_after = pattern.sub(_replace_match, text)

    # Second pass: find remaining non-ascii characters
    non_ascii_re = re.compile(r"[^\x00-\x7F]")
    remaining = non_ascii_re.findall(text_after)

    # We'll collect additional replacements here
    extra_replacements: Dict[str, Dict[str, Any]] = {}

    if remaining:
        # Count unique occurrences
        rem_counts = Counter(remaining)
        for ch, cnt in rem_counts.items():
            # Try transliterating using NFKD
            trans = unicodedata.normalize("NFKD", ch)
            ascii_equiv = "".join(c for c in trans if ord(c) < 128)
            if ascii_equiv:
                replacement = ascii_equiv
                # Replace all occurrences of ch with replacement if there is an ascii equivalent
                text_after = text_after.replace(ch, replacement)
            else:
                # No ASCII equivalent -> do NOT replace; report as unfixable.
                replacement = None

            # Report all occurrences of ch with replacement
            found_counter[ch] += cnt
            extra_replacements[ch] = {
                "replacement": replacement,
                "count": cnt,
                "codepoint": f"U+{ord(ch):04X}",
                "unfixable": True if replacement is None else False,
            }

    # Construct the replacements dict from found_counter and mapping
    replacements: Dict[str, Dict[str, Any]] = {}
    for ch, cnt in found_counter.items():
        replacements[ch] = {
            "replacement": mapping.get(ch, extra_replacements.get(ch, {}).get("replacement", "?")),
            "count": cnt,
            "codepoint": f"U+{ord(ch):04X}",
        }

    found_any = len(replacements) > 0

    return found_any, text_after, replacements
