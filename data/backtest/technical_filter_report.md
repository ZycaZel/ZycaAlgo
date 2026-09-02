# Would a technical confirmation filter help?

Tested on 2,977 of 3,561 backtested insider signals (the rest lacked enough price history for a 200-day average). Returns are **excess over SPY**. Every indicator is computed from bars dated strictly *before* the filing date, so nothing here uses information the strategy wouldn't have had.

A filter is only worth adding if the **passing** signals genuinely beat the ones it would have thrown away. A filter that keeps the good ones *and* the bad ones equally is just throwing away trades.

## `above_sma200`

Price above its 200-day moving average (long-term uptrend)

Passes: 924 signals · Filtered out: 2,053

| Horizon | Passes filter | Would be discarded | Difference | Worth it? |
|---|---|---|---|---|
| 5d | n=922, +1.75% | n=2,050, +2.74% | -0.99% (p=0.008) | no - and worse |
| 30d | n=922, -0.05% | n=2,049, +1.41% | -1.46% (p=0.052) | no - and worse |
| 90d | n=922, +2.30% | n=2,046, +0.50% | +1.80% (p=0.264) | no |

## `above_sma50`

Price above its 50-day moving average (medium-term trend)

Passes: 1,006 signals · Filtered out: 1,971

| Horizon | Passes filter | Would be discarded | Difference | Worth it? |
|---|---|---|---|---|
| 5d | n=1,004, +2.57% | n=1,968, +2.36% | +0.20% (p=0.609) | no |
| 30d | n=1,004, +1.64% | n=1,967, +0.61% | +1.03% (p=0.180) | no |
| 90d | n=1,004, +3.26% | n=1,964, -0.06% | +3.32% (p=0.031) | **yes** |

## `rsi_not_hot`

RSI(14) below 70 (not buying into an overbought move)

Passes: 2,800 signals · Filtered out: 177

| Horizon | Passes filter | Would be discarded | Difference | Worth it? |
|---|---|---|---|---|
| 5d | n=2,795, +2.37% | n=177, +3.40% | -1.03% (p=0.411) | no - and worse |
| 30d | n=2,794, +0.94% | n=177, +1.29% | -0.35% (p=0.849) | no - and worse |
| 90d | n=2,791, +1.00% | n=177, +2.03% | -1.03% (p=0.750) | no - and worse |

## `volume_surge`

Volume above its own 20-day average on the prior bar

Passes: 1,612 signals · Filtered out: 1,365

| Horizon | Passes filter | Would be discarded | Difference | Worth it? |
|---|---|---|---|---|
| 5d | n=1,612, +2.35% | n=1,360, +2.52% | -0.17% (p=0.641) | no - and worse |
| 30d | n=1,612, +0.66% | n=1,359, +1.31% | -0.65% (p=0.397) | no - and worse |
| 90d | n=1,610, +0.76% | n=1,358, +1.42% | -0.66% (p=0.649) | no - and worse |

## `near_high`

Within 25% of the 52-week high

Passes: 1,203 signals · Filtered out: 1,774

| Horizon | Passes filter | Would be discarded | Difference | Worth it? |
|---|---|---|---|---|
| 5d | n=1,203, +0.92% | n=1,769, +3.46% | -2.53% (p=0.000) | no - and worse |
| 30d | n=1,203, -0.53% | n=1,768, +1.97% | -2.50% (p=0.000) | no - and worse |
| 90d | n=1,203, -1.97% | n=1,765, +3.13% | -5.10% (p=0.000) | no - and worse |

## Does it survive out-of-sample?

Split at the backtest's own in-sample cutoff (**2024-12-18**). A filter that only works on one side of this line is a pattern found in the data, not a property of the strategy. Values are the difference between passing and discarded signals.

| Condition | Period | 5d | 30d | 90d |
|---|---|---|---|---|
| `above_sma200` | in-sample | -0.65% (p=0.153) | -1.78% (p=0.035) | +1.38% (p=0.464) |
| `above_sma200` | out-of-sample | -1.98% (p=0.001) | -1.06% (p=0.505) | +2.20% (p=0.499) |
| `above_sma50` | in-sample | +0.89% (p=0.072) | +1.21% (p=0.166) | +2.45% (p=0.176) |
| `above_sma50` | out-of-sample | -1.43% (p=0.020) | +0.37% (p=0.803) | +5.08% (p=0.100) |
| `rsi_not_hot` | in-sample | -2.10% (p=0.151) | -0.71% (p=0.725) | -1.64% (p=0.646) |
| `rsi_not_hot` | out-of-sample | +3.54% (p=0.092) | +1.80% (p=0.708) | +3.12% (p=0.693) |
| `volume_surge` | in-sample | -0.21% (p=0.630) | -0.69% (p=0.410) | -0.14% (p=0.938) |
| `volume_surge` | out-of-sample | -0.09% (p=0.878) | -0.48% (p=0.765) | -1.61% (p=0.525) |
| `near_high` | in-sample | -2.35% (p=0.000) | -2.43% (p=0.002) | -3.77% (p=0.023) |
| `near_high` | out-of-sample | -2.99% (p=0.000) | -2.97% (p=0.026) | -8.97% (p=0.000) |

## How to read this

A **yes** means signals passing that filter beat the ones it would discard by more than noise explains, at that horizon. **no** means the filter would cost you trades without buying anything measurable. A negative difference means the filter is actively selecting the *worse* half.

Five conditions across three horizons is fifteen tests, so roughly one 5% result is expected by chance. Treat a single marginal p-value with suspicion; a filter worth shipping should hold up across horizons, not appear at exactly one.
