#include "testlib.h"
#include <vector>
#include <numeric>
#include <algorithm>
#include <iostream>

using namespace std;

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    
    // Read parameters from command line or use defaults
    int n = opt<int>("n", rnd.next(2, 100));
    long long max_val = opt<long long>("max_val", 1000000000LL);
    
    // Generate nums array and ensure there is exactly one solution.
    int idx1 = rnd.next(0, n - 2);
    int idx2 = rnd.next(idx1 + 1, n - 1);
    
    // FIX: draw num1 from the half-range so that target = num1+num2 always
    // stays within [-max_val, max_val] (the validator constraint).
    // Without this, target could reach ±2×10^9 and the validator rejected the
    // test (root cause of the 3 'unusable' tests in the original run).
    long long half = max_val / 2;
    long long num1   = rnd.next(-half, half);
    long long num2   = rnd.next(-half, half);
    long long target = num1 + num2;  // |target| <= max_val guaranteed
    
    vector<long long> nums(n);
    nums[idx1] = num1;
    nums[idx2] = num2;
    
    for (int i = 0; i < n; ++i) {
        if (i == idx1 || i == idx2) continue;
        while (true) {
            long long val = rnd.next(-max_val, max_val);
            bool ok = true;
            for (int j = 0; j < i; ++j) {
                if (j == idx1 || j == idx2) continue;
                if (nums[j] + val == target) ok = false;
            }
            if (val + num1 == target || val + num2 == target) ok = false;
            if (ok) {
                nums[i] = val;
                break;
            }
        }
    }
    
    cout << n << " " << target << "\n";
    for (int i = 0; i < n; ++i) {
        cout << nums[i] << (i == n - 1 ? "" : " ");
    }
    cout << "\n";
    
    return 0;
}
