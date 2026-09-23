import os
import json
from pathlib import Path
from datetime import datetime, timezone

import requests


# =========================================================
# TELEGRAM
# =========================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# =========================================================
# FILTERS
# =========================================================

# New pair kitne minutes tak "new" maana jayega
MAX_AGE_MINUTES = 120

# Minimum liquidity
MIN_LIQUIDITY_USD = 5_000

# Minimum 24h volume
MIN_VOLUME_USD = 2_000

# Minimum transactions
MIN_BUYS = 5
MIN_SELLS = 2
MIN_TXNS = 7


# =========================================================
# CHAINS
# =========================================================

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


# =========================================================
# FILES / API
# =========================================================

STATE_FILE = Path("seen_pairs.json")

DEX_API = "https://api.dexscreener.com"


# =========================================================
# SEEN PAIRS
# =========================================================

def load_seen():
    if not STATE_FILE.exists():
        return set()

    try:
        data = json.loads(STATE_FILE.read_text())

        if not isinstance(data, list):
            return set()

        return set(data)

    except Exception as e:
        print("Seen file error:", e)
        return set()


def save_seen(seen):
    try:
        # State file ko manageable rakho
        items = list(seen)[-5000:]

        STATE_FILE.write_text(
            json.dumps(items, indent=2)
        )

    except Exception as e:
        print("Save seen error:", e)


# =========================================================
# TELEGRAM
# =========================================================

def telegram_send(message):
    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

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


# =========================================================
# DEXSCREENER - LATEST TOKEN PROFILES
# =========================================================

def get_latest_tokens_for_chain(chain):
    url = f"{DEX_API}/token-profiles/latest/v1"

    response = requests.get(
        url,
        timeout=20,
    )

    response.raise_for_status()

    profiles = response.json()

    if not isinstance(profiles, list):
        return []

    tokens = []

    for profile in profiles:

        if profile.get("chainId") != chain:
            continue

        token_address = profile.get("tokenAddress")

        if not token_address:
            continue

        if token_address not in tokens:
            tokens.append(token_address)

    return tokens


# =========================================================
# TOKEN PAIRS
# =========================================================

def get_token_pairs(chain, token):
    url = f"{DEX_API}/token-pairs/v1/{chain}/{token}"

    try:
        response = requests.get(
            url,
            timeout=20,
        )

        if response.status_code != 200:
            print(
                f"Pair API error: {chain} "
                f"{response.status_code}"
            )
            return []

        data = response.json()

        if not isinstance(data, list):
            return []

        return data

    except Exception as e:
        print(
            f"Pair request error: {chain} "
            f"{token}: {e}"
        )
        return []


# =========================================================
# PAIR AGE
# =========================================================

def age_minutes(pair):
    created = pair.get("pairCreatedAt")

    if not created:
        return None

    try:
        created_seconds = float(created) / 1000

        now = datetime.now(
            timezone.utc
        ).timestamp()

        age = (
            now - created_seconds
        ) / 60

        return age

    except Exception:
        return None


# =========================================================
# NUMBER HELPERS
# =========================================================

def safe_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def safe_int(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


# =========================================================
# TRANSACTION DATA
# =========================================================

def get_transaction_data(pair):
    txns = pair.get("txns") or {}

    # New coins ke liye 1h data zyada useful hai.
    # Agar 1h nahi hai to 5m, phir 24h.
    periods = [
        ("h1", txns.get("h1")),
        ("m5", txns.get("m5")),
        ("h24", txns.get("h24")),
    ]

    for period_name, period in periods:

        if not isinstance(period, dict):
            continue

        buys = safe_int(
            period.get("buys")
        )

        sells = safe_int(
            period.get("sells")
        )

        if buys > 0 or sells > 0:
            return (
                period_name,
                buys,
                sells,
            )

    return (
        "none",
        0,
        0,
    )


# =========================================================
# CHECK PAIR
# =========================================================

def check_pair(pair):

    # -----------------------------------------------------
    # AGE
    # -----------------------------------------------------

    age = age_minutes(pair)

    if age is None:
        return False

    if age < 0:
        return False

    if age > MAX_AGE_MINUTES:
        return False


    # -----------------------------------------------------
    # LIQUIDITY
    # -----------------------------------------------------

    liquidity = pair.get("liquidity") or {}

    liquidity_usd = safe_float(
        liquidity.get("usd")
    )

    if liquidity_usd < MIN_LIQUIDITY_USD:
        return False


    # -----------------------------------------------------
    # VOLUME
    # -----------------------------------------------------

    volume = pair.get("volume") or {}

    volume_24h = safe_float(
        volume.get("h24")
    )

    if volume_24h < MIN_VOLUME_USD:
        return False


    # -----------------------------------------------------
    # TRANSACTIONS
    # -----------------------------------------------------

    _, buys, sells = get_transaction_data(
        pair
    )

    if buys < MIN_BUYS:
        return False

    if sells < MIN_SELLS:
        return False

    if (buys + sells) < MIN_TXNS:
        return False


    return True


# =========================================================
# FORMAT NUMBERS
# =========================================================

def format_number(value):

    value = safe_float(value)

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


# =========================================================
# BUILD TELEGRAM MESSAGE
# =========================================================

def build_message(pair):

    chain = pair.get(
        "chainId",
        "unknown"
    )

    dex = pair.get(
        "dexId",
        "unknown"
    )

    base = pair.get(
        "baseToken"
    ) or {}

    name = base.get(
        "name",
        "Unknown"
    )

    symbol = base.get(
        "symbol",
        "UNKNOWN"
    )

    address = base.get(
        "address",
        ""
    )

    age = age_minutes(pair)

    liquidity = (
        pair.get("liquidity") or {}
    ).get("usd", 0)

    volume = (
        pair.get("volume") or {}
    ).get("h24", 0)

    market_cap = pair.get(
        "marketCap"
    )

    fdv = pair.get(
        "fdv"
    )

    dex_url = pair.get(
        "url",
        ""
    )

    period, buys, sells = (
        get_transaction_data(pair)
    )

    return (
        "🚨 NEW COIN ALERT 🚨\n\n"

        f"🪙 {name} ({symbol})\n\n"

        f"⛓ Chain: {chain}\n"
        f"🏦 DEX: {dex}\n"
        f"⏱ Age: {age:.1f} min\n"
        f"📊 Txn Period: {period}\n\n"

        f"💧 Liquidity: "
        f"{format_number(liquidity)}\n"

        f"📈 Volume 24h: "
        f"{format_number(volume)}\n"

        f"💰 Market Cap: "
        f"{format_number(market_cap)}\n"

        f"📊 FDV: "
        f"{format_number(fdv)}\n\n"

        f"🟢 Buys: {buys}\n"
        f"🔴 Sells: {sells}\n"
        f"📊 Total Txns: {buys + sells}\n\n"

        f"📍 Contract:\n"
        f"{address}\n\n"

        f"🔗 DexScreener:\n"
        f"{dex_url}\n\n"

        "⚠️ NEW TOKEN = HIGH RISK\n"
        "Check contract, liquidity, holders, "
        "taxes and honeypot risk before trading."
    )


# =========================================================
# SCAN
# =========================================================

def scan():

    seen = load_seen()

    found = 0

    checked = 0

    passed = 0


    print("===================================")
    print("CryptoRadarAKBot scan started")
    print("===================================")


    for chain in CHAINS:

        print(
            f"\n🔎 Scanning chain: {chain}"
        )

        try:

            tokens = (
                get_latest_tokens_for_chain(
                    chain
                )
            )

            print(
                f"Found {len(tokens)} "
                f"latest token profiles"
            )


            # API load control
            for token in tokens[:30]:

                pairs = get_token_pairs(
                    chain,
                    token
                )

                for pair in pairs:

                    checked += 1

                    pair_address = pair.get(
                        "pairAddress"
                    )

                    if not pair_address:
                        continue


                    # -------------------------------------------------
                    # IMPORTANT:
                    # Pehle filter check hoga.
                    # Filter fail hone par seen nahi hoga.
                    # -------------------------------------------------

                    if pair_address in seen:
                        continue


                    if not check_pair(pair):

                        continue


                    passed += 1


                    # -------------------------------------------------
                    # Alert send
                    # -------------------------------------------------

                    message = build_message(
                        pair
                    )

                    try:

                        telegram_send(
                            message
                        )

                        print(
                            "✅ ALERT SENT:",
                            chain,
                            pair.get(
                                "baseToken",
                                {}
                            ).get(
                                "symbol",
                                "UNKNOWN"
                            )
                        )

                        found += 1

                        # Sirf successful Telegram
                        # ke baad seen mark karo.
                        seen.add(
                            pair_address
                        )

                    except Exception as e:

                        print(
                            "❌ Telegram error:",
                            e
                        )

        except Exception as e:

            print(
                f"❌ {chain} error: {e}"
            )


    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    save_seen(seen)


    print("\n===================================")
    print(
        f"Pairs checked: {checked}"
    )
    print(
        f"Pairs passed filters: {passed}"
    )
    print(
        f"Alerts sent: {found}"
    )
    print("Scan finished.")
    print("===================================")


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    scan()