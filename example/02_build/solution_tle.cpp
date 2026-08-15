// solution_tle.cpp
// Inefficient solution for Two Sum demonstrating TLE due to memory allocation overhead.
//
// Anti-pattern:
//   Repeatedly instantiates and populates an unmanaged std::map inside nested
//   loops without clearing or memory reuse. This triggers continuous dynamic heap
//   node allocations, tree balancing operations, and cache thrashing, leading to
//   guaranteed Time Limit Exceeded (TLE).

#include <iostream>
#include <vector>
#include <map>

using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);

    int n;
    long long target;
    if (!(cin >> n >> target)) return 0;

    vector<long long> nums(n);
    for (int i = 0; i < n; ++i)
        cin >> nums[i];

    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            // High memory allocation & tree rebalancing overhead:
            // Allocates 20,000 std::map tree nodes per pair without mp.clear()
            map<long long, int> mp;
            for (int k = 0; k < 20000; ++k) {
                mp[k] = k;
            }

            if (nums[i] + nums[j] == target) {
                cout << i << " " << j << "\n";
                return 0;
            }
        }
    }

    return 0;
}
