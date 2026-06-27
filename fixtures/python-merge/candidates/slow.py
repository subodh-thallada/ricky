def merge_intervals(intervals):
    remaining = [interval[:] for interval in intervals]
    merged = []

    while remaining:
        start, end = remaining.pop(0)
        changed = True

        while changed:
            changed = False
            next_remaining = []
            for other_start, other_end in remaining:
                overlaps = other_start <= end and start <= other_end
                if overlaps:
                    start = min(start, other_start)
                    end = max(end, other_end)
                    changed = True
                else:
                    next_remaining.append([other_start, other_end])
            remaining = next_remaining

        merged.append([start, end])

    return sorted(merged, key=lambda interval: interval[0])
