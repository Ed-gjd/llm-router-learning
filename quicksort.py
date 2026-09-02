def quicksort(arr, low=0, high=None):
    """In-place quicksort of arr[low:high] using Lomuto partition."""
    if high is None:
        high = len(arr) - 1

    def partition(lo, hi):
        pivot = arr[hi]
        i = lo - 1
        for j in range(lo, hi):
            if arr[j] <= pivot:
                i += 1
                arr[i], arr[j] = arr[j], arr[i]
        arr[i + 1], arr[hi] = arr[hi], arr[i + 1]
        return i + 1

    def sort(lo, hi):
        if lo < hi:
            p = partition(lo, hi)
            sort(lo, p - 1)
            sort(p + 1, hi)

    sort(low, high)
    return arr


if __name__ == "__main__":
    samples = [
        [3, 6, 8, 10, 1, 2, 1],
        [],
        [5],
        [9, 8, 7, 6, 5],
        [1, 2, 3, 4, 5],
    ]
    for s in samples:
        result = quicksort(s[:])
        assert result == sorted(s), f"{result} != {sorted(s)}"
    print("All tests passed.")
