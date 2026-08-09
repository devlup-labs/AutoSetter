# Two Sum

You are given an array of integers `nums` and an integer `target`, return indices of the two numbers such that they add up to `target`.

You may assume that each input would have exactly one solution, and you may not use the same element twice.

You can return the answer in any order.

### Input Format
- The first line contains two integers: `n` (the size of the array) and `target`.
- The second line contains `n` space-separated integers `nums[i]`.

### Output Format
- Print two space-separated integers representing the 0-based indices of the two numbers.

### Constraints
- $2 \le n \le 1000$
- $-10^9 \le nums[i] \le 10^9$
- $-10^9 \le target \le 10^9$
- Only one valid answer exists.

### Examples
**Example 1**
Input:
```
4 9
2 7 11 15
```
Output:
```
0 1
```
Explanation:
Because nums[0] + nums[1] == 9, we return 0 1.
