"""
E*TRADE Account Sync — pulls real positions from your brokerage account.

This module authenticates with E*TRADE via OAuth, fetches your account list
and portfolio, and returns structured position data that can seed the paper
trading portfolio.

Requires valid CONSUMER_KEY and CONSUMER_SECRET in config.ini.
"""

import json
import logging
import configparser
import webbrowser
from rauth import OAuth1Service

import os

logger = logging.getLogger("my_logger")

config = configparser.ConfigParser()
# Resolve config.ini relative to this file's directory (etrade_python_client/)
_config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config.read(os.path.join(_config_dir, "config.ini"))


class AccountSync:
    """Non-interactive (where possible) E*TRADE account sync."""

    def __init__(self):
        self.session = None
        self.base_url = None
        self.accounts = []

    # ------------------------------------------------------------------ #
    #  Authentication
    # ------------------------------------------------------------------ #

    def authenticate(self, use_sandbox: bool = False) -> bool:
        """
        Run OAuth flow. Opens a browser for the user to authorize,
        then prompts for the verification code.

        :param use_sandbox: if True, use sandbox URL (fake data)
        :return: True if authentication succeeded
        """
        consumer_key = config["DEFAULT"]["CONSUMER_KEY"]
        consumer_secret = config["DEFAULT"]["CONSUMER_SECRET"]

        # Use production keys when available and not in sandbox mode
        if not use_sandbox:
            prod_key = config["DEFAULT"].get("PROD_CONSUMER_KEY", "")
            prod_secret = config["DEFAULT"].get("PROD_CONSUMER_SECRET", "")
            if prod_key and "PASTE" not in prod_key:
                consumer_key = prod_key
                consumer_secret = prod_secret

        if "PLEASE_ENTER" in consumer_key or "PASTE" in consumer_key:
            logger.error("E*TRADE API keys not configured in config.ini")
            return False

        etrade = OAuth1Service(
            name="etrade",
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            request_token_url="https://api.etrade.com/oauth/request_token",
            access_token_url="https://api.etrade.com/oauth/access_token",
            authorize_url="https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
            base_url="https://api.etrade.com",
        )

        self.base_url = (
            config["DEFAULT"]["SANDBOX_BASE_URL"] if use_sandbox
            else config["DEFAULT"]["PROD_BASE_URL"]
        )

        try:
            request_token, request_token_secret = etrade.get_request_token(
                params={"oauth_callback": "oob", "format": "json"}
            )

            authorize_url = etrade.authorize_url.format(etrade.consumer_key, request_token)
            webbrowser.open(authorize_url)

            # This is the one interactive step — user must paste the code
            text_code = input(
                "\nE*TRADE authorization opened in your browser.\n"
                "Please accept and enter the verification code: "
            )

            self.session = etrade.get_auth_session(
                request_token,
                request_token_secret,
                params={"oauth_verifier": text_code},
            )
            logger.info("E*TRADE authentication successful")
            return True

        except Exception as exc:
            logger.error("E*TRADE authentication failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    #  Account listing
    # ------------------------------------------------------------------ #

    def fetch_accounts(self) -> list[dict]:
        """
        Fetch the list of E*TRADE accounts.
        Returns list of account dicts with accountId, accountIdKey, accountDesc, etc.
        """
        if not self.session:
            logger.error("Not authenticated — call authenticate() first")
            return []

        url = f"{self.base_url}/v1/accounts/list.json"
        try:
            response = self.session.get(url, header_auth=True)
            logger.debug("Account list URL: %s", url)
            logger.debug("Account list status: %s", response.status_code)
            logger.debug("Account list body: %s", response.text[:500] if response.text else "empty")

            if response.status_code != 200:
                print(f"  API returned status {response.status_code}")
                print(f"  URL: {url}")
                if response.text:
                    print(f"  Response: {response.text[:300]}")
                logger.error("Account list API error: %s — %s", response.status_code, response.text[:200])
                return []

            data = response.json()
            accounts = (
                data.get("AccountListResponse", {})
                .get("Accounts", {})
                .get("Account", [])
            )
            # Filter out closed accounts
            self.accounts = [a for a in accounts if a.get("accountStatus") != "CLOSED"]
            logger.info("Found %d active account(s)", len(self.accounts))
            return self.accounts

        except Exception as exc:
            logger.error("Failed to fetch accounts: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    #  Portfolio fetch
    # ------------------------------------------------------------------ #

    def fetch_positions(self, account: dict) -> list[dict]:
        """
        Fetch positions for a specific account.

        :param account: account dict (must have 'accountIdKey')
        :return: list of position dicts with symbol, qty, avg_cost, etc.
        """
        if not self.session:
            logger.error("Not authenticated")
            return []

        account_key = account.get("accountIdKey")
        if not account_key:
            logger.error("Account missing accountIdKey")
            return []

        url = f"{self.base_url}/v1/accounts/{account_key}/portfolio.json"
        try:
            response = self.session.get(url, header_auth=True)

            if response.status_code == 204:
                logger.info("Account %s has no positions", account.get("accountId", "?"))
                return []

            if response.status_code != 200:
                logger.error("Portfolio API error: %s", response.status_code)
                return []

            data = response.json()
            positions = []

            portfolio_data = data.get("PortfolioResponse", {}).get("AccountPortfolio", [])
            for acct_portfolio in portfolio_data:
                for position in acct_portfolio.get("Position", []):
                    product = position.get("Product", {})
                    symbol = product.get("symbol")
                    security_type = product.get("securityType", "")

                    # Only sync equity positions (skip options, mutual funds, etc.)
                    if security_type not in ("EQ", ""):
                        logger.debug("Skipping non-equity position: %s (%s)", symbol, security_type)
                        continue

                    if not symbol:
                        continue

                    qty = position.get("quantity", 0)
                    if qty <= 0:
                        continue  # skip short positions for paper trading

                    avg_cost = position.get("pricePaid", 0)
                    last_price = position.get("Quick", {}).get("lastTrade")
                    market_value = position.get("marketValue")
                    total_gain = position.get("totalGain")
                    description = position.get("symbolDescription", "")

                    positions.append({
                        "symbol": symbol,
                        "qty": int(qty),
                        "avg_cost": round(float(avg_cost), 2),
                        "last_price": round(float(last_price), 2) if last_price else None,
                        "market_value": round(float(market_value), 2) if market_value else None,
                        "total_gain": round(float(total_gain), 2) if total_gain else None,
                        "description": description,
                        "security_type": security_type,
                    })

            logger.info(
                "Fetched %d equity position(s) from account %s",
                len(positions), account.get("accountId", "?"),
            )
            return positions

        except Exception as exc:
            logger.error("Failed to fetch positions: %s", exc)
            return []

    def fetch_balance(self, account: dict) -> dict | None:
        """
        Fetch account balance.

        :param account: account dict (must have 'accountIdKey')
        :return: dict with cash_balance, total_value, buying_power
        """
        if not self.session:
            return None

        account_key = account.get("accountIdKey")
        url = f"{self.base_url}/v1/accounts/{account_key}/balance.json"
        params = {
            "instType": account.get("institutionType", "BROKERAGE"),
            "realTimeNAV": "true",
        }
        headers = {"consumerkey": config["DEFAULT"]["CONSUMER_KEY"]}

        try:
            response = self.session.get(url, header_auth=True, params=params, headers=headers)
            if response.status_code != 200:
                return None

            data = response.json().get("BalanceResponse", {})
            computed = data.get("Computed", {})
            rtv = computed.get("RealTimeValues", {})

            return {
                "account_id": data.get("accountId"),
                "description": data.get("accountDescription"),
                "total_value": rtv.get("totalAccountValue"),
                "cash_buying_power": computed.get("cashBuyingPower"),
                "margin_buying_power": computed.get("marginBuyingPower"),
            }
        except Exception as exc:
            logger.error("Failed to fetch balance: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    #  Convenience: sync to paper portfolio format
    # ------------------------------------------------------------------ #

    def sync_to_seed_positions(self, account: dict) -> dict[str, dict]:
        """
        Fetch positions and convert to SEED_POSITIONS format.

        Returns: {"AAPL": {"qty": 50, "avg_cost": 170.00}, ...}
        """
        positions = self.fetch_positions(account)
        seed = {}
        for pos in positions:
            seed[pos["symbol"]] = {
                "qty": pos["qty"],
                "avg_cost": pos["avg_cost"],
            }
        return seed

    def sync_all(self, account_index: int = 0, use_sandbox: bool = False) -> dict:
        """
        Full sync: authenticate, pick account, fetch positions + balance.

        :param account_index: which account to sync (0 = first brokerage account)
        :param use_sandbox: use sandbox API
        :return: dict with 'positions', 'balance', 'cash', 'seed_positions'
        """
        if not self.authenticate(use_sandbox=use_sandbox):
            return {"error": "Authentication failed"}

        accounts = self.fetch_accounts()
        if not accounts:
            return {"error": "No accounts found"}

        # Pick the requested account (default: first brokerage account)
        brokerage_accounts = [a for a in accounts if a.get("institutionType") == "BROKERAGE"]
        if not brokerage_accounts:
            return {"error": "No brokerage accounts found"}

        if account_index >= len(brokerage_accounts):
            account_index = 0

        account = brokerage_accounts[account_index]
        logger.info(
            "Syncing account: %s (%s)",
            account.get("accountId"), account.get("accountDesc", ""),
        )

        positions = self.fetch_positions(account)
        balance = self.fetch_balance(account)
        seed = {p["symbol"]: {"qty": p["qty"], "avg_cost": p["avg_cost"]} for p in positions}

        cash = 0.0
        if balance and balance.get("cash_buying_power"):
            cash = float(balance["cash_buying_power"])

        return {
            "account_id": account.get("accountId"),
            "account_desc": account.get("accountDesc", ""),
            "positions": positions,
            "balance": balance,
            "cash": cash,
            "seed_positions": seed,
        }


# ---------------------------------------------------------------------------
#  CLI entry point — run this directly to sync and print positions
# ---------------------------------------------------------------------------
def main():
    """Sync positions from E*TRADE and print them."""
    import sys

    sync = AccountSync()
    use_sandbox = "--sandbox" in sys.argv

    print("\n" + "=" * 60)
    print("  E*TRADE Account Sync")
    print("  Pulling real positions into paper trading")
    print("=" * 60)

    result = sync.sync_all(use_sandbox=use_sandbox)

    if "error" in result:
        print(f"\nError: {result['error']}")
        return

    print(f"\nAccount: {result['account_id']} — {result['account_desc']}")
    if result["balance"]:
        b = result["balance"]
        if b.get("total_value"):
            print(f"Total Value: ${b['total_value']:,.2f}")
        if b.get("cash_buying_power"):
            print(f"Cash Buying Power: ${b['cash_buying_power']:,.2f}")

    print(f"\nPositions ({len(result['positions'])}):")
    print(f"  {'Symbol':<8} {'Qty':>6} {'Avg Cost':>10} {'Last':>10} {'Value':>12} {'Gain':>10}")
    print("  " + "-" * 60)
    for p in result["positions"]:
        last = f"${p['last_price']:,.2f}" if p['last_price'] else "N/A"
        mval = f"${p['market_value']:,.2f}" if p['market_value'] else "N/A"
        gain = f"${p['total_gain']:,.2f}" if p['total_gain'] else "N/A"
        print(
            f"  {p['symbol']:<8} {p['qty']:>6} "
            f"${p['avg_cost']:>9,.2f} "
            f"{last:>10} "
            f"{mval:>12} "
            f"{gain:>10}"
        )

    print(f"\nSEED_POSITIONS for trading_config.py:")
    print("SEED_POSITIONS = {")
    for sym, pos in result["seed_positions"].items():
        print(f'    "{sym}": {{"qty": {pos["qty"]}, "avg_cost": {pos["avg_cost"]}}},')
    print("}")

    # Also save to a JSON file for the web dashboard to pick up
    with open("synced_positions.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nFull sync data saved to synced_positions.json")


if __name__ == "__main__":
    main()
