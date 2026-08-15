// solution_brute.cpp
// Brute-force correct solution for Two Sum.
// Checks every pair (i, j) with i < j.
// Time complexity:  O(n^2)   — correct but slower than the optimal solution
// Space complexity: O(1)
// With n <= 1000 this is fast enough for the given time limit, but would TLE
// if the constraint were raised to n ~ 10^6.

#include <iostream>
#include <vector>

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

    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            if (nums[i] + nums[j] == target) {
                cout << i << " " << j << "\n";
                return 0;
            }
        }
    }

    // Problem guarantees a solution exists, so we should never reach here.
    return 0;
}
