# Does the exit rule suit the signal?

Every one of 3,157 backtested signals replayed under five exit rules on real daily bars. Returns are **excess over SPY** across each position's own holding window, so a rule is never credited simply for holding through a rising market.

| Exit rule | Mean excess | Median | Hit rate | Days held | vs. live |
|---|---|---|---|---|---|
| Live rules (15% stop, trail, 90d) | +0.25% | -3.28% | 43% | 45 | — |
| Fixed 5-day hold | +2.41% | +0.94% | 57% | 5 | **+2.16pp (p=0.000)** |
| Fixed 10-day hold | +2.26% | +0.71% | 55% | 10 | **+2.00pp (p=0.000)** |
| Fixed 20-day hold | +1.74% | -0.60% | 47% | 20 | **+1.49pp (p=0.000)** |
| 5-day hold with 15% stop | +2.38% | +0.94% | 57% | 5 | **+2.13pp (p=0.000)** |

## Reading this

The comparison against the live rule is paired: every rule trades exactly the same signals, and the only thing that differs is when it exits. That removes signal selection from the comparison entirely and leaves the exit timing as the sole variable.

A result here says nothing about whether the entry signal is good. It says only whether the system is holding its positions for the right length of time.

Daily closes are used, so a stop is treated as triggering on a close through the level rather than an intraday touch. That makes the live rule look slightly *better* here than it would in practice, not worse.
