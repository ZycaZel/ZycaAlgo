# ZycaAlgo insider-buy signal backtest

Window: 2023q3 - 2025q2  |  Signals: 4014  |  With price data: 3561

In-sample cutoff (70th percentile by filing date): 2024-12-18


Both a paired t-test (on the mean) and a Wilcoxon signed-rank test (on the median, distribution-free - doesn't assume symmetric returns) are reported. Where they disagree, treat the Wilcoxon result as the more conservative read, since the return distributions here are right-skewed (a handful of large winners pull the mean above the median at every horizon).


## 5-trading-day horizon

| Period | n | mean excess ret | median excess ret | hit rate | t-stat | t-test p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| Full sample | 3550 | +2.38% | +0.92% | 57.1% | 14.05 | 0.0000 | 0.0000 |
| In-sample | 2465 | +2.31% | +0.89% | 56.3% | 11.21 | 0.0000 | 0.0000 |
| Out-of-sample | 1085 | +2.54% | +0.98% | 58.8% | 8.57 | 0.0000 | 0.0000 |

## 30-trading-day horizon

| Period | n | mean excess ret | median excess ret | hit rate | t-stat | t-test p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| Full sample | 3542 | +0.90% | -1.58% | 44.6% | 2.46 | 0.0140 | 0.0002 |
| In-sample | 2457 | +1.17% | -1.30% | 45.3% | 2.83 | 0.0047 | 0.0638 |
| Out-of-sample | 1085 | +0.28% | -2.17% | 42.8% | 0.38 | 0.7033 | 0.0001 |

## 90-trading-day horizon

| Period | n | mean excess ret | median excess ret | hit rate | t-stat | t-test p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| Full sample | 3518 | +0.80% | -4.53% | 40.8% | 1.06 | 0.2877 | 0.0000 |
| In-sample | 2441 | +1.24% | -3.37% | 43.3% | 1.30 | 0.1948 | 0.0000 |
| Out-of-sample | 1077 | -0.19% | -6.33% | 35.1% | -0.16 | 0.8748 | 0.0000 |
