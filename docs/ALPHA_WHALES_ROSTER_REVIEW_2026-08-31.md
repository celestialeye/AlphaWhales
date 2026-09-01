# Alpha Whales Final Roster Recommendation

**Decision date:** August 31, 2026
**Performance data through:** August 28, 2026
**Status:** Approved roster implemented and refreshed.

## The three decision rules

1. **INCLUDE** when the AlphaWhales full-window 13F estimate beats both SPY and
   QQQ.
2. **INCLUDE + FLAG** when the 13F estimate does not beat both, but verified
   official or self-reported performance beats both.
3. **REMOVE** in every other case.

For rule 2, reported performance must identify the exact vehicle or composite,
period, fee basis, and same-period SPY and QQQ results. Beating only one
benchmark, using a different currency without adjustment, citing media reports,
or lacking an exact comparable period does not qualify.

## Final answer

| Decision | Managers |
|---|---:|
| **INCLUDE** | **24** |
| **INCLUDE + FLAG** | **5** |
| **REMOVE** | **83** |
| **Total unique managers evaluated** | **112** |

The final Alpha Whales roster contains 29 managers:

- All 24 qualify directly under rule 1.
- Five current incumbents are retained as explicitly approved exceptions.
- The other 21 current incumbents are removed.
- All 62 non-duplicate Dataroma managers are removed.

## Investment-style taxonomy

Roster qualification and investment style are separate dimensions. The UI
groups managers by philosophy rather than by the date they entered the roster:

| Style | Managers |
|---|---|
| **Value & Contrarian** | Sanders Capital, ARGA, Blackhill, Li Lu, Buffett |
| **Quality Growth** | Yarbrough Capital, Silvant Capital, Blue Whale, Randolph Co |
| **Technology & Innovation** | Newlands Management, Value Aligned, Voyager Global, Analog Century, Kinetic Partners, Styrax, Keywise, Dock Street, Laffont |
| **Opportunistic & Concentrated** | Lingotto, First Beijing, Crake, Sosin, Ackman |
| **Diversified & Systematic** | Systematic Alpha, Y.D. More, Traynor Capital, Petredis, Weatherly, German American Bancorp |

The five approved exceptions retain these style labels and also display the
separate exception flag.

“Removed” means removed from the configured Alpha Whales roster. Historical SEC
filings remain in the screening database.

## What happens to the current roster

| Current-roster action | Count |
|---|---:|
| Remain in roster under rule 1 | 0 |
| Remain as approved flagged exceptions | 5 |
| Removed from roster | 21 |
| New rule-1 managers added | 24 |
| **Net configured roster size** | **29, up from 26** |

Most of the current roster is replaced:

1. **Li Lu, Sosin, Laffont, Buffett, and Ackman remain as flagged exceptions.**
   They do not meet the full-window rule-1 test, so the UI must keep their
   exception status visible.
2. **The other 21 incumbents are removed.** They neither pass the rule-1
   performance test nor have an approved exception.

The exception label prevents Buffett or Ackman from being mistaken for
full-window 13F winners while preserving their manager intelligence.

## Rule-1 selections: 24 managers

These managers passed the approved holdings screen and their full-window 13F
estimates beat both SPY and QQQ.

| Manager | CIK | Est. CAGR | vs SPY | vs QQQ | Max drawdown |
|---|---|---:|---:|---:|---:|
| Sanders Capital | `0001508097` | 17.76% | +4.97 pp | +3.52 pp | -26.91% |
| Newlands Management Operations | `0001908450` | 32.72% | +11.93 pp | +4.87 pp | -31.01% |
| Value Aligned Research Advisors | `0001963565` | 51.48% | +30.42 pp | +22.51 pp | -48.28% |
| Lingotto Investment Management | `0001732768` | 30.54% | +17.75 pp | +16.30 pp | -33.45% |
| ARGA Investment Management | `0001556915` | 16.28% | +3.50 pp | +2.05 pp | -26.45% |
| Voyager Global Management | `0001849753` | 22.14% | +7.44 pp | +4.12 pp | -33.28% |
| Yarbrough Capital | `0001767686` | 20.19% | +6.63 pp | +3.82 pp | -33.14% |
| Systematic Alpha Investments | `0001806755` | 16.16% | +3.38 pp | +1.93 pp | -20.35% |
| Silvant Capital Management | `0001738728` | 14.68% | +1.90 pp | +0.45 pp | -29.43% |
| First Beijing Investment | `0001701717` | 27.65% | +5.57 pp | +1.19 pp | -36.38% |
| Crake Asset Management | `0001789082` | 22.87% | +10.09 pp | +8.64 pp | -28.45% |
| Analog Century Management | `0001753384` | 39.18% | +26.40 pp | +24.95 pp | -46.34% |
| Kinetic Partners Management | `0001911448` | 28.81% | +8.11 pp | +1.21 pp | -27.40% |
| Blackhill Capital | `0000872162` | 21.01% | +8.23 pp | +6.78 pp | -35.18% |
| Y.D. More Investments | `0001870364` | 31.99% | +11.52 pp | +4.67 pp | -16.34% |
| Blue Whale Capital | `0001801547` | 17.34% | +4.56 pp | +3.11 pp | -43.99% |
| Traynor Capital Management | `0001666786` | 17.82% | +5.03 pp | +3.58 pp | -37.41% |
| Styrax Capital | `0001904897` | 25.09% | +7.57 pp | +2.38 pp | -25.10% |
| Keywise Capital Management (HK) | `0001474069` | 40.21% | +27.42 pp | +25.97 pp | -48.19% |
| Petredis Investment Advisors | `0001964544` | 27.85% | +7.39 pp | +0.53 pp | -21.01% |
| Weatherly Asset Management | `0000934745` | 15.17% | +2.39 pp | +0.94 pp | -21.60% |
| German American Bancorp | `0000714395` | 14.58% | +1.79 pp | +0.34 pp | -24.95% |
| Dock Street Asset Management | `0001172779` | 18.60% | +5.82 pp | +4.37 pp | -34.72% |
| Randolph Co | `0001475150` | 15.12% | +2.34 pp | +0.89 pp | -19.68% |

### Why these 24 qualify

The exact screen was:

- Four-quarter median reported value of at least $1 billion.
- At least five current direct-stock positions.
- At least 50% of reported non-option value in direct stocks.
- At least 50% top-ten direct-stock concentration.
- Concentration requirement met in eight of eight quarters.
- At least three positions continuously held at 3% or more for 12 months.
- Full-window 13F estimate above both SPY and QQQ.

The completed mapping-repair campaign evaluated 5,574 managers and increased
valid five-year estimates to 1,846. Rerunning the screen produced 24
qualifiers, up from the 16 found before the repaired mappings were fully
published.

### Risk flags inside the included roster

These do not reverse inclusion because every manager passes rule 1:

- **Value Aligned, Keywise, and Analog Century:** estimated maximum drawdowns
  between approximately 46% and 48%.
- **Blue Whale:** approximately 44% maximum drawdown.
- **German American Bancorp:** only +0.34 percentage points above QQQ and is a
  bank holding company rather than a conventional external fund manager.
- **Silvant, Petredis, Weatherly, and Randolph:** QQQ excess below one
  percentage point.
- **Systematic Alpha, Silvant, Y.D. More, Traynor, Petredis, Weatherly, and
  German American:** broad portfolios despite meeting the persistent-best-bet
  requirements.

## Approved exceptions: 5 managers

| Manager | Why retained | Required flag |
|---|---|---|
| Li Lu / Himalaya | The 3Y clone beat SPY and QQQ; the full window narrowly missed both | Recent-window exception |
| Sosin / CAS | The 3Y clone produced 50.9% CAGR and large excess versus both benchmarks; full-window pricing is incomplete | Incomplete full window |
| Laffont / Coatue | The 3Y clone beat SPY by 7.3 pp and QQQ by 3.3 pp; full-window pricing is incomplete | Incomplete full window |
| Buffett / Berkshire | Exceptional verified long-term shareholder record includes operating businesses, cash, private assets, foreign holdings, and capital allocation outside the 13F sleeve | Actual record differs from 13F clone |
| Ackman / Pershing Square | Strong official strategy record is not reproduced by delayed 13F following | Actual record differs from 13F clone |

These five remain in the configured roster with `is_exception: true`. Screening,
investor cards, and detail pages display the exception badge.

## Current incumbents: 5 retained, 21 removed

No current incumbent has a full-window AlphaWhales estimate above both SPY and
QQQ. Five remain only because the user explicitly approved them as exceptions.

### Full-window 13F estimate available but does not beat both

| Incumbent | Est. CAGR | vs SPY | vs QQQ | Decision |
|---|---:|---:|---:|---|
| Kantesaria / Valley Forge | 10.87% | -1.91 pp | -3.36 pp | **REMOVE** |
| Li Lu / Himalaya | 12.66% | -0.12 pp | -1.57 pp | **INCLUDE + FLAG** |
| Rochon / Giverny | 7.31% | -5.47 pp | -6.92 pp | **REMOVE** |
| Massey / AltaRock | 9.48% | -3.30 pp | -4.75 pp | **REMOVE** |
| Hohn / TCI | 10.82% | -1.97 pp | -3.42 pp | **REMOVE** |
| Buffett / Berkshire | 11.66% | -1.12 pp | -2.57 pp | **INCLUDE + FLAG** |
| Ackman / Pershing Square | 2.88% | -9.91 pp | -11.36 pp | **INCLUDE + FLAG** |
| Terry Smith / Fundsmith | 3.71% | -9.08 pp | -10.53 pp | **REMOVE** |
| Polen Focus Growth | 1.25% | -11.54 pp | -12.99 pp | **REMOVE** |

### Full-window 13F estimate unavailable

Two of the 17 unavailable managers are approved exceptions:

- Sosin / CAS Investment Partners — **INCLUDE + FLAG**
- Laffont / Coatue — **INCLUDE + FLAG**

The remaining 15 are removed:

- Bloomstran / Semper Augustus
- Pabrai / Dalal Street
- Nygren / Harris Associates
- Akre / Akre Capital
- Klarman / Baupost
- Dorsey / Dorsey Asset Management
- Baker / Atreides
- Sacerdote / Whale Rock
- Ellenbogen / Durable Capital
- Greenberg / Brave Warrior
- Simons / Meritage
- Hong / ShawSpring
- Sandler / Eminence
- Halvorsen / Viking
- Mandel / Lone Pine

### Why other well-known managers are removed

- **Bloomstran:** Semper letters report long-term S&P 500 outperformance, but
  the current evidence does not contain the exact same-period QQQ comparison
  required by rule 2.
- **Rochon:** the reported record is gross and CAD-denominated against a blended
  global benchmark, so it is not an exact SPY-and-QQQ comparison.
- **Fundsmith:** the official GBP fund record is compared with MSCI World GBP,
  not an exact same-period SPY and QQQ pair.
- **Baupost:** the configured CIK points to Tiger Global, and Baupost values
  require a 1,000x scale correction. Current evidence is not valid for
  inclusion.

## Dataroma evaluation

Dataroma lists 83 Superinvestors. Twenty-one overlap with the current incumbent
review, leaving **62 non-duplicate Dataroma managers**.

Result:

| Decision | Non-duplicate Dataroma managers |
|---|---:|
| **INCLUDE** | **0** |
| **INCLUDE + FLAG** | **0** |
| **REMOVE** | **62** |

None of the 62 has a current full-window AlphaWhales estimate above both SPY
and QQQ. Based on the evidence available when this report was finalized, none
also has a verified exact reported-performance comparison that beats both
benchmarks. Each therefore falls under rule 3.

<details>
<summary><strong>Open the complete 62-manager Dataroma evaluation</strong></summary>

Every manager below is **REMOVE** under current evidence.

1. Abrams Bison Investments — `0001317588`
2. AKO Capital — `0001376879`
3. Atlantic Investment Management — `0001063296`
4. Century Management / Arnold Van Den Berg — `0001142062`
5. Gates Foundation Trust — `0001166559`
6. Miller Value Partners — `0001135778`
7. Fairholme Capital — `0001056831`
8. Oakcliff Capital — `0001657335`
9. Icahn Capital Management — `0000921669`
10. Tiger Global Management — `0001167483`
11. Davis Advisors — `0001036325`
12. Third Point — `0001040273`
13. Abrams Capital Management — `0001358706`
14. Greenlight Capital — `0001079114`
15. Matrix Asset Advisors — `0001016287`
16. Wedgewood Partners — `0000859804`
17. Appaloosa Management — `0001656456`
18. Dodge & Cox — `0000200217`
19. H&H International Investment — `0001759760`
20. First Eagle Investment Management — `0001325447`
21. First Pacific Advisors — `0001377581`
22. Chou Associates — `0001389403`
23. Engaged Capital — `0001559771`
24. Greenhaven Associates — `0000846222`
25. Conifer Management — `0001773994`
26. Aquamarine Capital — `0001953324`
27. Sound Shore — `0000820124`
28. Hillman Capital Management — `0001314620`
29. Oaktree Capital Management — `0000949509`
30. Jensen Investment Management — `0001106129`
31. Egerton Capital — `0001581811`
32. Ariel Investments — `0000936753`
33. Greenlea Lane Capital — `0001766504`
34. Kahn Brothers — `0001039565`
35. Maverick Capital — `0000934639`
36. Leon Cooperman — `0000898382`
37. Lindsell Train — `0001484150`
38. Mairs & Power — `0001070134`
39. Southeastern Asset Management — `0000807985`
40. Scion Asset Management — `0001649339`
41. Muhlenkamp — `0001133219`
42. Trian Fund Management — `0001345471`
43. Punch Card Management — `0001631664`
44. Fairfax Financial Holdings — `0000915191`
45. Pzena Investment Management — `0001027796`
46. RV Capital — `0001766596`
47. Ruane, Cunniff & Goldfarb — `0001720792`
48. Patient Capital Management — `0001854794`
49. Causeway Capital Management — `0001165797`
50. Check Capital Management — `0001032814`
51. Third Avenue Management — `0001099281`
52. Markel Group — `0001096343`
53. Gardner Russo & Quinn — `0000860643`
54. Makaira Partners — `0001540866`
55. Torray Funds — `0000098758`
56. Triple Frond Partners — `0001454502`
57. Tweedy Browne — `0000732905`
58. ValueAct Capital — `0001418814`
59. Vulcan Value Partners — `0001556785`
60. Weitz Investment Management — `0000883965`
61. Cantillon Capital Management — `0001279936`
62. Yacktman Asset Management — `0000905567`

</details>

## Data and methodology notes

- The 13F result is a hypothetical disclosure-lagged reported long-equity
  sleeve, not an actual fund or account return.
- It uses filing chronology, next-SPY-session implementation, reported
  quarter-end weights, adjusted market prices, and common-date SPY/QQQ
  comparisons.
- Full-window estimates cover up to approximately five years.
- Rule 2 is intentionally conservative. A manager is not assumed to beat QQQ
  merely because an official document shows S&P 500 outperformance.
- Dataroma is a discovery source only. SEC filings remain the holdings source
  of record.

## Implemented roster and refresh plan

1. `roster.json` now stores the 24 rule-1 managers and five approved
   exceptions.
2. Investor Screening can add, add-and-flag, or remove individual and selected
   managers without editing Python source.
3. Roster changes update the running application and invalidate historical
   caches whose roster fingerprint no longer matches.
4. The compact screening snapshot is rebuilt so roster-only filtering and
   aliases match the 29-manager roster.
5. Latest and 20-quarter configured-fund caches are refreshed through
   EdgarTools. Existing all-manager performance facts are reused; changing the
   roster does not trigger another performance campaign.
6. The decision is tied to performance run
   `7aa7d429ef89437ba20542966c4b0066`.
7. The prior 26-manager roster definition, latest caches, and 19 historical
   snapshots are preserved under
   `cache/roster_backups/2026-08-31-prior-roster/` with a SHA-256 manifest.

Baupost is no longer in the roster. Its CIK and value-scale defects remain data
quality work for the universe-wide screening foundation, but they no longer
block this roster revamp.

### Completed refresh result

- 29 of 29 latest manager caches loaded.
- All 19 historical cache files contain the 29-manager roster and a roster
  fingerprint.
- Historical availability is 29 of 29 through June 2023 and declines in older
  periods where newer managers had not yet filed.
- The compact snapshot contains 5,574 managers with reusable performance facts,
  returns the 24 rule-1 qualifiers, and identifies all five roster exceptions.

## Sources

- Dataroma Superinvestors:
  https://www.dataroma.com/m/managers.php
- Pershing Square Holdings NAV:
  https://pershingsquareholdings.com/performance/nav/
- Berkshire Hathaway 2025 Annual Report:
  https://www.berkshirehathaway.com/2025ar/2025ar.pdf
- Akre Focus:
  https://www.akrefund.com/
- Oakmark Fund:
  https://oakmark.com/our-funds/oakmark-fund/
- Semper Augustus performance information:
  https://www.semperaugustus.com/Investment-Performance-Information/
- Giverny Rochon Global Portfolio:
  https://givernycapital.com/wp-content/uploads/2024/01/rendements-rochon-global-english-2023.pdf
- Fundsmith factsheet:
  https://www.fundsmith.co.uk/factsheet
- Polen Focus Growth GIPS report:
  https://www.polencapital.com/sites/default/files/2023-09/Polen-Capital-Focus-Growth-GIPS-Report.pdf
