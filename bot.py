import os
import time
import json
from pathlib import Path
from datetime import datetime, timezone

import requests


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# ===== FILTERS =====
MAX_AGE_MINUTES = 60
MIN_LIQUIDITY_USD = 10_000
MIN_VOLUME_USD = 5_000
MIN_BUYS = 15
MIN_SELLS = 8
MIN_TXNS = 30

# Multiple chains
CHAINS = [
    "solana",
    "ethereum",
    "base",
    "bsc",
    "arbitrum",
    "polygon",
    "avalanche",
    "optimism",
]

STATE_FILE = Path("seen_pairs.json")

DEX_API = "https://api.dexscreener.com"


def load_seen():
    if not STATE_FILE.exists():
        return set()

    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return set()


def save_seen(seen):
    # Keep state reasonably small
    items = list(seen)[-5000:]
    STATE_FILE.write_text(json.dumps(items))


def telegram_send(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    response.raise_for_status()


def get_pairs_for_chain(chain):
    """
    DEX Screener doesn't expose a single documented
    'new pairs for every chain' endpoint.

    We therefore use latest token profiles to discover
    recently surfaced tokens, then query their pairs.
    """

    url = f"{DEX_API}/token-profiles/latest/v1"

    response = requests.get(url, timeout=20)
    response.raise_for_status()

    profiles = response.json()

    if not isinstance(profiles, list):
        return []

    tokens = []

    for profile in profiles:
        if profile.get("chainId") == chain:
            address = profile.get("tokenAddress")

            if address:
                tokens.append(address)

    return tokens


def get_token_pairs(chain, token):
    url = f"{DEX_API}/token-pairs/v1/{chain}/{token}"

    response = requests.get(url, timeout=20)

    if response.status_code != 200:
        return []

    data = response.json()

    if not isinstance(data, list):
        return []

    return data


def age_minutes(pair):
    created = pair.get("pairCreatedAt")

    if not created:
        return None

    # DexScreener timestamp is milliseconds
    created_seconds = created / 1000

    now = datetime.now(timezone.utc).timestamp()

    return (now - created_seconds) / 60


def check_pair(pair):
    age = age_minutes(pair)

    if age is None:
        return False

    if age < 0 or age > MAX_AGE_MINUTES:
        return False

    liquidity = pair.get("liquidity") or {}
    liquidity_usd = float(liquidity.get("usd") or 0)

    if liquidity_usd < MIN_LIQUIDITY_USD:
        return False

    volume = pair.get("volume") or {}

    # Prefer 24h volume when available
    volume_usd = float(volume.get("h24") or 0)

    if volume_usd < MIN_VOLUME_USD:
        return False

    txns = pair.get("txns") or {}

    # Try 24h first, then 5m/1h if available
    period = txns.get("h24") or txns.get("h1") or txns.get("m5") or {}

    buys = int(period.get("buys") or 0)
    sells = int(period.get("sells") or 0)

    if buys < MIN_BUYS:
        return False

    if sells < MIN_SELLS:
        return False

    if buys + sells < MIN_TXNS:
        return False

    return True


def format_number(value):
    try:
        value = float(value)

        if value >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"

        if value >= 1_000:
            return f"${value / 1_000:.1f}K"

        return f"${value:.0f}"

    except Exception:
        return "N/A"


def build_message(pair):
    chain = pair.get("chainId", "unknown")
    dex = pair.get("dexId", "unknown")

    base = pair.get("baseToken") or {}

    name = base.get("name", "Unknown")
    symbol = base.get("symbol", "UNKNOWN")
    address = base.get("address", "")

    age = age_minutes(pair)

    liquidity = (pair.get("liquidity") or {}).get("usd", 0)
    volume = (pair.get("volume") or {}).get("h24", 0)

    txns = pair.get("txns") or {}
    period = txns.get("h24") or txns.get("h1") or txns.get("m5") or {}

    buys = period.get("buys", 0)
    sells = period.get("sells", 0)

    market_cap = pair.get("marketCap")
    fdv = pair.get("fdv")

    dex_url = pair.get("url", "")

    return (
        "🚨 NEW COIN ALERT\n\n"
        f"🪙 {name} ({symbol})\n"
        f"⛓ Chain: {chain}\n"
        f"🏦 DEX: {dex}\n"
        f"⏱ Age: {age:.1f} min\n\n"
        f"💧 Liquidity: {format_number(liquidity)}\n"
        f"📈 Volume 24h: {format_number(volume)}\n"
        f"📊 Market Cap: {format_number(market_cap) if market_cap else 'N/A'}\n"
        f"📊 FDV: {format_number(fdv) if fdv else 'N/A'}\n\n"
        f"🟢 Buys: {buys}\n"
        f"🔴 Sells: {sells}\n\n"
        f"📍 Contract:\n{address}\n\n"
        f"🔗 DexScreener:\n{dex_url}\n\n"
        "⚠️ NEW TOKEN = HIGH RISK\n"
        "Do your own contract/liquidity/holder checks."
    )


def scan():
    seen = load_seen()

    found = 0

    for chain in CHAINS:
        try:
            tokens = get_pairs_for_chain(chain)

            # Avoid excessive API requests
            for token in tokens[:30]:

                pairs = get_token_pairs(chain, token)

                for pair in pairs:

                    pair_address = pair.get("pairAddress")

                    if not pair_address:
                        continue

                    if pair_address in seen:
                        continue

                    # Mark as seen even if it doesn't pass filters,
                    # preventing repeated processing.
                    seen.add(pair_address)

                    if check_pair(pair):
                        message = build_message(pair)

                        try:
                            telegram_send(message)
                            print(
                                f"ALERT: {chain} "
                                f"{pair.get('baseToken', {}).get('symbol')}"
                            )
                            found += 1

                        except Exception as e:
                            print("Telegram error:", e)

        except Exception as e:
            print(f"{chain} error:", e)

    save_seen(seen)

    print(f"Scan finished. Alerts: {found}")


if __name__ == "__main__":
    scan()