# Polymath - Universal Prediction Market Arbitrage Bot

A Python-based arbitrage bot that monitors Polymarket and Kalshi prediction markets in real-time, automatically discovers matching markets using fuzzy matching, and identifies risk-free arbitrage opportunities across all market categories.

## How It Works

1. **Fetch** active markets from both Polymarket (Gamma + CLOB APIs) and Kalshi (REST API v2)
2. **Match** markets across platforms using topic-filtered fuzzy string matching (rapidfuzz)
3. **Detect** arbitrage when the combined cost of opposing positions is less than $1.00
4. **Alert** via a real-time web dashboard with WebSocket updates
5. **Execute** trades automatically when enabled (optional, requires API credentials)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy env config
cp .env.example .env

# Run the bot
python main.py
```

Open `http://localhost:8000` in your browser to view the dashboard.

## Configuration

Edit `.env` to tune the scanner:

| Variable | Default | Description |
|---|---|---|
| `SCAN_INTERVAL_SECONDS` | 5 | Seconds between scan cycles |
| `MIN_PROFIT_CENTS` | 2 | Minimum profit (in cents) to flag an opportunity |
| `MATCH_SIMILARITY_THRESHOLD` | 80 | Fuzzy match threshold (0-100%) |
| `AUTO_EXECUTE` | false | Enable automatic trade execution |
| `MAX_POSITION_SIZE_USD` | 100 | Max USD per arbitrage trade |
| `MAX_DAILY_LOSS_USD` | 50 | Daily loss limit before halting execution |

### Auto-Execution (Optional)

To enable auto-execution, you need credentials for both platforms:

- **Polymarket**: Set `POLYMARKET_PRIVATE_KEY` (Polygon wallet private key with USDC.e)
- **Kalshi**: Set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web dashboard |
| `/api/opportunities` | GET | Current arbitrage opportunities (sorted by ROI) |
| `/api/matched-markets` | GET | All matched market pairs with similarity scores |
| `/api/stats` | GET | Scanner statistics |
| `/api/settings` | POST | Update scanner settings at runtime |
| `/ws` | WebSocket | Real-time opportunity updates |

## Project Structure

```
polymath/
  main.py              # Entry point
  config.py            # Settings (loaded from .env)
  src/
    markets/
      polymarket.py    # Polymarket API client
      kalshi.py        # Kalshi API client
      models.py        # Shared data models
    matching/
      matcher.py       # Topic-filtered fuzzy market matcher
    arbitrage/
      detector.py      # Arbitrage detection engine
      executor.py      # Trade execution (with safety guardrails)
    scanner.py         # Background scan loop
  web/
    api.py             # FastAPI routes + WebSocket
    static/            # Dashboard frontend (HTML/JS/CSS)
```

## How Arbitrage Detection Works

For each matched market pair, the bot checks both directions:

- **Direction A**: Buy YES on Kalshi + Buy NO on Polymarket
- **Direction B**: Buy YES on Polymarket + Buy NO on Kalshi

If the total cost (including worst-case platform fees) is less than $1.00, the difference is a risk-free profit. One side is guaranteed to pay out $1.00 when the event resolves.

## Disclaimer

This software is for educational and research purposes. Trading on prediction markets involves risk. "Risk-free" refers to the mathematical arbitrage condition; practical execution carries timing, slippage, and liquidity risks. Always verify opportunities manually before committing capital.
