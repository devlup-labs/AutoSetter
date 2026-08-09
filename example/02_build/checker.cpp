#include "testlib.h"
#include <vector>

using namespace std;

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    
    int n = inf.readInt();
    long long target = inf.readLong();
    vector<long long> nums(n);
    for (int i = 0; i < n; ++i) {
        nums[i] = inf.readLong();
    }
    
    int ans1 = ans.readInt();
    int ans2 = ans.readInt();
    
    int ouf1 = ouf.readInt(0, n - 1, "index1");
    int ouf2 = ouf.readInt(0, n - 1, "index2");
    
    if (ouf1 == ouf2) {
        quitf(_wa, "Indices must be distinct, got %d and %d", ouf1, ouf2);
    }
    
    if (nums[ouf1] + nums[ouf2] != target) {
        quitf(_wa, "Sum of elements at indices %d and %d is %lld, expected %lld", ouf1, ouf2, nums[ouf1] + nums[ouf2], target);
    }
    
    quitf(_ok, "Correct pair found with sum %lld", target);
}
