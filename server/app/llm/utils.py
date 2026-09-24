import difflib
from typing import Tuple


def find_offsets(target: str, full_text: str) -> Tuple[int, int]:
    """
    Find the start and end offsets of a target string within a full text.
    Returns a tuple of (start_offset, end_offset).
    """
    start_offset = full_text.find(target)
    if start_offset == -1:
        # Run a fuzzy search if exact match not found
        matcher = difflib.SequenceMatcher(None, full_text, target)
        match = matcher.find_longest_match(0, len(full_text), 0, len(target))
        if match.size == 0:
            return -1, -1  # No match found

        start_offset = match.a  # Start index of the match in full_text
        end_offset = start_offset + match.size

    if start_offset == -1:
        return -1, -1

    end_offset = start_offset + len(target)
    return start_offset, end_offset
