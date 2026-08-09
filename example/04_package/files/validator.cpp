#include "testlib.h"
#include <vector>

using namespace std;

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    
    int n = inf.readInt(2, 1000, "n");
    inf.readSpace();
    long long target = inf.readLong(-1000000000LL, 1000000000LL, "target");
    inf.readEoln();
    
    vector<long long> nums(n);
    for (int i = 0; i < n; ++i) {
        nums[i] = inf.readLong(-1000000000LL, 1000000000LL, format("nums[%d]", i));
        if (i < n - 1) {
            inf.readSpace();
        }
    }
    inf.readEoln();
    inf.readEof();
    
    bool found = false;
    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            if (nums[i] + nums[j] == target) {
                found = true;
            }
        }
    }
    ensuref(found, "No pair sums to target");
    
    return 0;
}
