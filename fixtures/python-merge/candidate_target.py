def merge_intervals(intervals):
    """Merge overlapping [start, end] intervals."""
    if not intervals:
        return []

    ordered = sorted(intervals, key=lambda interval: interval[0])
    merged = [ordered[0][:]]

    for start, end in ordered[1:]:
        current = merged[-1]
        if start <= current[1]:
            current[1] = max(current[1], end)
        else:
            merged.append([start, end])

    return merged
