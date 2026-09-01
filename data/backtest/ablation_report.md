# Ablation study: which filters carry the edge?

Based on 3,561 backtested insider-buy signals. All returns are **excess over SPY** across the same window, so a positive number means beating the index rather than merely rising with it.

`p` on each arm tests whether that arm's excess return differs from zero. The **difference** row is a Welch's t-test between the two arms - the question of whether the split actually separates anything, which a gap in the means alone cannot answer.

## Cluster Buying

2+ distinct insiders buying the same ticker vs a single insider

| Horizon | cluster (2+ insiders) (n, mean, p) | solitary (1 insider) (n, mean, p) | Difference | Significant? |
|---|---|---|---|---|
| 5d | n=398, +3.62%, p=0.000 | n=3,152, +2.23%, p=0.000 | +1.39% (p=0.016) | **yes** |
| 30d | n=398, +1.83%, p=0.127 | n=3,144, +0.78%, p=0.042 | +1.05% (p=0.403) | no |
| 90d | n=395, +1.65%, p=0.519 | n=3,123, +0.69%, p=0.377 | +0.96% (p=0.721) | no |

## Insider Role

Officers vs Directors (rows counted as an officer where both apply)

| Horizon | Officer (n, mean, p) | Director (n, mean, p) | Difference | Significant? |
|---|---|---|---|---|
| 5d | n=1,516, +2.41%, p=0.000 | n=2,034, +2.36%, p=0.000 | +0.05% (p=0.876) | no |
| 30d | n=1,515, +1.10%, p=0.072 | n=2,027, +0.75%, p=0.094 | +0.35% (p=0.643) | no |
| 90d | n=1,502, -0.24%, p=0.825 | n=2,016, +1.58%, p=0.133 | -1.81% (p=0.226) | no |

## Purchase Size

Purchases at or above the median ($313,845) vs below it

| Horizon | large (>= $313,845) (n, mean, p) | small (< $313,845) (n, mean, p) | Difference | Significant? |
|---|---|---|---|---|
| 5d | n=1,775, +3.03%, p=0.000 | n=1,775, +1.73%, p=0.000 | +1.30% (p=0.000) | **yes** |
| 30d | n=1,767, +1.12%, p=0.038 | n=1,775, +0.68%, p=0.171 | +0.44% (p=0.547) | no |
| 90d | n=1,753, +0.70%, p=0.483 | n=1,765, +0.90%, p=0.425 | -0.20% (p=0.894) | no |

## Reading this honestly

An arm that beats the other on its mean but shows **no** in the significance column has not been shown to be better - the gap is within what noise of this sample size produces. Splitting an already-filtered set also shrinks each arm, so a real effect can fail to reach significance here purely for lack of data; absence of evidence is not evidence of absence.

This table runs one test per split per horizon. Across that many tests, roughly one result at the 5% threshold is expected from chance alone, so a single borderline `p` just under 0.05 deserves much less weight than a result that clears it comfortably. No multiple-comparison correction is applied here - the raw p-values are reported so the arithmetic stays visible rather than buried in an adjustment.

These arms are all *subsets* of the filtered signal set. They cannot say anything about the filters applied before this stage - the 10b5-1 exclusion and the $100K floor remove their rows upstream, so no return was ever computed for them. Testing those would mean re-running the backtest with each filter disabled.
