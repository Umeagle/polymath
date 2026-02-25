from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # API base URLs
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    kalshi_api_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    # Scanner
    scan_interval_seconds: int = 60
    min_profit_cents: float = 2.0
    match_similarity_threshold: int = 80
    auto_execute: bool = False

    # Execution credentials (only needed when auto_execute=True)
    polymarket_private_key: str = ""
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: str = ""

    # Risk management
    max_position_size_usd: float = 100.0
    max_daily_loss_usd: float = 50.0

    # Fees (as fractions)
    polymarket_fee_rate: float = 0.02
    kalshi_fee_rate: float = 0.07

    # Market fetch limits
    max_polymarket_markets: int = 5000
    max_kalshi_markets: int = 15000

    # Rate limiting
    kalshi_max_rps: int = 10
    polymarket_max_rps: int = 10


settings = Settings()
