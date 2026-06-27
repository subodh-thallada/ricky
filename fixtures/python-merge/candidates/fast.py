def merge_intervals(intervals):
    if not intervals:
        return []

    ordered = sorted((start, end) for start, end in intervals)
    merged = []
    append = merged.append

    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            if end > current_end:
                current_end = end
        else:
            append([current_start, current_end])
            current_start, current_end = start, end

    append([current_start, current_end])
    return merged
