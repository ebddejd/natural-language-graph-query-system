# Journey Agent evaluation cases (draft)

Use confirmed endpoints UW → Conestoga Mall unless the input explicitly provides places.
Start each numbered case in a fresh session unless a prior turn is specified.
Freeze the clock at 2027-05-03 08:00 America/Toronto. Accept equivalent place names.
Score semantic changes after merge; also check that unrelated constraints and endpoints
are preserved. An invented restriction fails whole-request scoring.

| ID | User input or sequence | Expected outcome |
|---|---|---|
| 01 | From UW to Conestoga Mall by bus. | Origin UW, destination Conestoga Mall, transit. |
| 02 | 从 Conestoga Mall 步行到 UW。 | Reversed endpoints, walk only. |
| 03 | Drive from Waterloo Public Library Main to UW. | Library Main → UW, drive only. |
| 04 | 明早九点从 UW 出发去 Conestoga Mall。 | UW → mall, depart May 4 at 09:00. |
| 05 | I need to arrive tomorrow by 9 am. | Arrive May 4 at 09:00. |
| 06 | 今天下午三点出发。 | Depart May 3 at 15:00. |
| 07 | Drive if transit takes over three quarters of an hour. | Transit with conditional driving, threshold 45 min. |
| 08 | 公交超过三刻钟就查驾车。 | Threshold 45 min. |
| 09 | Bus first; if it takes over 45 mins, try driving. | Threshold 45 min. |
| 10 | 从 UW 去 Conestoga Mall，bus 超过 45 min 就 drive。 | UW → mall, transit with fallback at 45 min. |
| 11 | 公交不超过一小时就坐公交，否则看看开车。 | Fallback threshold 60 min. |
| 12 | 公交太久就开车。 | Ask how long; no invented threshold or recommendation. |
| 13 | Keep total walking under or equal to 10 minutes. | Max total walking 10 min, no driving threshold. |
| 14 | 一次车都不想换。 | Max transfers 0. |
| 15 | One transfer is fine, but no more. | Max transfers 1. |
| 16 | 坐公交，但避开 ION。 | Transit, avoid rail. |
| 17 | Compare walking and driving. | Both modes, no conditional fallback. |
| 18 | 找走路最少的方案。 | Rank by least walking. |
| 19 | Prefer the shortest distance. | Rank by shortest distance. |
| 20 | 换乘越少越好。 | Rank by fewest transfers; no invented hard cap. |
| 21 | Start with case 07, then “算了，改成 30 分钟。” | Threshold becomes 30; endpoints and transit retained. |
| 22 | Start with case 13, then “Make that 5 minutes.” | Walking cap becomes 5; no change to driving threshold. |
| 23 | Start with case 14, then “一次换乘也可以。” | Max transfers becomes 1. |
| 24 | Start with case 16, then “ION is fine now.” | Rail exclusion removed; retain endpoints. |
| 25 | Start with case 13, then “取消步行时间限制。” | Walking cap removed. |
| 26 | Start with case 04, then “Same time, but I need to arrive by then.” | Keep date/time; change departure to arrival. |
| 27 | Start with case 07, then “把终点改成 Waterloo Public Library Main。” | Request new destination with confirmation; retain UW and 45 min. |
| 28 | New session, no endpoints: “Get me to school.” | Clarify school; no invented coordinates or verified route. |
| 29 | No route options yet: “Choose option two.” | No successful selection of nonexistent option. |
| 30 | 有三个已展示的选项时：“选第二条。” | Select the second displayed option without changing the constraints. |

## Deterministic routing and end-to-end fixtures

Use fastest preference and feasible driving duration 40 min unless overridden.
Start with fallback threshold 45 min. Report both actual tool calls and top recommendation.

| Fixture | Transit minutes | Driving queried? | Top result |
|---|---|---|---|
| A | 44 | No | Transit |
| B | 45 | No | Transit |
| C | 45 min + 1 s | Yes | Drive |
| D | 46 | Yes | Drive |
| E | No feasible route | Yes | Drive |
| F | 44 and 50, both feasible | No | Transit, 44 min |
| G | 46; drive takes 55 | Yes | Transit, 46 min |
| H | No feasible transit or driving route | Yes | No verified recommendation |
| I | 35; case 21, driving takes 25 | No on turn 1; yes on turn 2 | Transit → drive; confirmed destination retained |

## Measurement protocol

Pin the model identifier and prompt version; request temperature zero when supported and
record the actual setting. Run each extraction case three independent times with the same
clock, fixtures and initial state. Report field accuracy with denominators, whole-case
accuracy, clarification and unexpected-field errors, and three-run semantic agreement.
Identical wrong results count as consistent but incorrect. Record input/output and any
cached token usage with dated provider prices; flag unknown usage rather than treating
it as free. Report estimated cost separately from billed cost. Score final recommendations
against the fixtures, with a separate end-to-end run through model and routing.
