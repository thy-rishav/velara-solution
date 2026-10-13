# VELARA Launch Tracker — Solution

Automated analysis tool for the VELARA 30-country launch tracker. Detects data defects, calculates slippage days per country, and ranks by impact.

## Files

| File | Purpose |
|---|---|
| `solve.py` | Main analysis script — reads data pack, outputs JSON to stdout |
| `velara_config.json` | Runtime configuration for the evaluation agent |
| `requirements.txt` | Python dependencies (openpyxl) |

## Usage

```bash
python3 solve.py <pack_dir>
```

Where `<pack_dir>` contains `VELARA_tracker.xlsx` and `dashboard_status.csv`.

## Output

Single JSON object to stdout with:
- `findings[]` — 19 defects detected (codes, locations, evidence)
- `risk_ranking[]` — all 30 countries ranked by impact (slippage × forecast volume)

## Results Summary

**Top 10 at-risk countries:**

| Rank | Country | Slippage | Impact | Driver |
|---|---|---|---|---|
| 1 | Canada | 150d | 105M | Starting dose unavailable |
| 2 | United States | 21d | 67M | Plant setup after shipment |
| 3 | Poland | 158d | 47M | Starting dose unavailable |
| 4 | Brazil | 18d | 16M | Demand loaded too late |
| 5 | Spain | 30d | 13M | Plant setup after shipment |
| 6 | Romania | 109d | 12M | Demand never loaded |
| 7 | Colombia | 81d | 11M | Demand never loaded |
| 8 | Peru | 154d | 9M | Starting dose unavailable |
| 9 | Czechia | 35d | 3M | Demand loaded too late |
| 10 | Finland | 49d | 3M | Demand loaded too late |
