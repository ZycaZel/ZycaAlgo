# ZycaAlgo insider-buy signal backtest

Window: 2023q3 - 2026q1  |  Signals: 5805  |  With price data: 5277

In-sample cutoff (70th percentile by filing date): 2025-07-24


Both a paired t-test (on the mean) and a Wilcoxon signed-rank test (on the median, distribution-free - doesn't assume symmetric returns) are reported. Where they disagree, treat the Wilcoxon result as the more conservative read, since the return distributions here are right-skewed (a handful of large winners pull the mean above the median at every horizon).


## 5-trading-day horizon

| Period | n | mean excess ret | median excess ret | hit rate | t-stat | t-test p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| Full sample | 5256 | +2.54% | +1.09% | 58.4% | 18.52 | 0.0000 | 0.0000 |
| In-sample | 3659 | +2.42% | +0.91% | 56.9% | 13.87 | 0.0000 | 0.0000 |
| Out-of-sample | 1597 | +2.82% | +1.50% | 61.8% | 13.44 | 0.0000 | 0.0000 |

## 30-trading-day horizon

| Period | n | mean excess ret | median excess ret | hit rate | t-stat | t-test p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| Full sample | 5248 | +1.39% | -1.13% | 45.9% | 4.56 | 0.0000 | 0.0035 |
| In-sample | 3652 | +0.78% | -1.69% | 44.3% | 2.18 | 0.0295 | 0.0000 |
| Out-of-sample | 1596 | +2.78% | -0.11% | 49.5% | 4.85 | 0.0000 | 0.2750 |

## 90-trading-day horizon

| Period | n | mean excess ret | median excess ret | hit rate | t-stat | t-test p | Wilcoxon p |
|---|---|---|---|---|---|---|---|
| Full sample | 5207 | +1.26% | -4.51% | 40.9% | 1.94 | 0.0523 | 0.0000 |
| In-sample | 3628 | +0.93% | -4.79% | 40.4% | 1.15 | 0.2483 | 0.0000 |
| Out-of-sample | 1579 | +2.01% | -4.05% | 42.0% | 1.89 | 0.0589 | 0.0000 |
