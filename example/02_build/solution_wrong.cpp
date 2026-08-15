// solution_wrong.cpp
// INTENTIONALLY BUGGY solution — used to test judge WA detection.
//
// Classic subtle bug:
//   Sorts the array to apply the optimal O(n log n) two-pointer technique,
//   but outputs the indices (l, r) in the SORTED array instead of mapping
//   them back to the original array indices.

#include <iostream>
#include <vector>
#include <algorithm>

using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);

    int n;
    long long target;
    cin >> n >> target;

    vector<long long> nums(n);
    for (int i = 0; i < n; ++i)
        cin >> nums[i];

    vector<long long> sorted_nums = nums;
    sort(sorted_nums.begin(), sorted_nums.end());

    int l = 0, r = n - 1;
    while (l < r) {
        long long sum = sorted_nums[l] + sorted_nums[r];
        if (sum == target) {
            // BUG: outputs indices in sorted_nums instead of original nums
            cout << l << " " << r << "\n";
            return 0;
        } else if (sum < target) {
            l++;
        } else {
            r--;
        }
    }

    return 0;
}
