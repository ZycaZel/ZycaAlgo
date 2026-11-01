# Ablation study: which filters carry the edge?

Based on 5,277 backtested insider-buy signals. All returns are **excess over SPY** across the same window, so a positive number means beating the index rather than merely rising with it.

`p` on each arm tests whether that arm's excess return differs from zero. The **difference** row is a Welch's t-test between the two arms - the question of whether the split actually separates anything, which a gap in the means alone cannot answer.

## Cluster Buying

2+ distinct insiders buying the same ticker vs a single insider

| Horizon | cluster (2+ insiders) (n, mean, p) | solitary (1 insider) (n, mean, p) | Difference | Significant? |
|---|---|---|---|---|
| 5d | n=602, +3.67%, p=0.000 | n=4,654, +2.40%, p=0.000 | +1.27% (p=0.006) | **yes** |
| 30d | n=602, +2.16%, p=0.018 | n=4,646, +1.29%, p=0.000 | +0.86% (p=0.370) | no |
| 90d | n=600, +2.61%, p=0.339 | n=4,607, +1.08%, p=0.091 | +1.53% (p=0.586) | no |

## Insider Role

Officers vs Directors (rows counted as an officer where both apply)

| Horizon | Officer (n, mean, p) | Director (n, mean, p) | Difference | Significant? |
|---|---|---|---|---|
| 5d | n=2,233, +2.48%, p=0.000 | n=3,023, +2.60%, p=0.000 | -0.12% (p=0.670) | no |
| 30d | n=2,232, +1.51%, p=0.003 | n=3,016, +1.30%, p=0.001 | +0.21% (p=0.743) | no |
| 90d | n=2,214, +0.23%, p=0.798 | n=2,993, +2.02%, p=0.027 | -1.79% (p=0.162) | no |

## Purchase Size

Purchases at or above the median ($338,095) vs below it

| Horizon | large (>= $338,095) (n, mean, p) | small (< $338,095) (n, mean, p) | Difference | Significant? |
|---|---|---|---|---|
| 5d | n=2,628, +3.13%, p=0.000 | n=2,628, +1.96%, p=0.000 | +1.17% (p=0.000) | **yes** |
| 30d | n=2,620, +1.61%, p=0.000 | n=2,628, +1.17%, p=0.003 | +0.45% (p=0.463) | no |
| 90d | n=2,600, +1.36%, p=0.139 | n=2,607, +1.16%, p=0.206 | +0.21% (p=0.873) | no |

## Reading this honestly

An arm that beats the other on its mean but shows **no** in the significance column has not been shown to be better - the gap is within what noise of this sample size produces. Splitting an already-filtered set also shrinks each arm, so a real effect can fail to reach significance here purely for lack of data; absence of evidence is not evidence of absence.

This table runs one test per split per horizon. Across that many tests, roughly one result at the 5% threshold is expected from chance alone, so a single borderline `p` just under 0.05 deserves much less weight than a result that clears it comfortably. No multiple-comparison correction is applied here - the raw p-values are reported so the arithmetic stays visible rather than buried in an adjustment.

These arms are all *subsets* of the filtered signal set. They cannot say anything about the filters applied before this stage - the 10b5-1 exclusion and the $100K floor remove their rows upstream, so no return was ever computed for them. Testing those would mean re-running the backtest with each filter disabled.
