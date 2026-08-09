#include <iostream>
#include <vector>
#include <unordered_map>

using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);
    int n;
    long long target;
    if (!(cin >> n >> target)) return 0;
    vector<long long> nums(n);
    unordered_map<long long, int> val_to_idx;
    int ans1 = -1, ans2 = -1;
    for (int i = 0; i < n; ++i) {
        cin >> nums[i];
        long long complement = target - nums[i];
        if (val_to_idx.count(complement)) {
            ans1 = val_to_idx[complement];
            ans2 = i;
        }
        val_to_idx[nums[i]] = i;
    }
    if (ans1 > ans2) swap(ans1, ans2);
    cout << ans1 << " " << ans2 << "\n";
    return 0;
}
