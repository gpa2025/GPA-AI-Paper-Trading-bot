"""
Performance report: Bot portfolio vs SPY & QQQ benchmarks.
Prints a console summary and displays a matplotlib chart.
"""

import sqlite3
import os
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from datetime import datetime

DB_PATH = "trading_bot.db"


def load_portfolio_daily(db_path: str) -> pd.Series:
    """Load end-of-day portfolio values from the database."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        """SELECT DATE(timestamp) as date, total_value
           FROM portfolio_snapshots
           WHERE total_value > 0
           GROUP BY DATE(timestamp)
           HAVING timestamp = MAX(timestamp)
           ORDER BY date ASC""",
        conn,
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["total_value"]


def load_benchmark(symbol: str, start: str, end: str) -> pd.Series:
    """Download benchmark daily close prices from Yahoo Finance."""
    data = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if data.empty:
        return pd.Series(dtype=float)
    close = data["Close"].squeeze()
    close.index = close.index.tz_localize(None)
    return close


def compute_returns(series: pd.Series) -> dict:
    """Compute total return, annualized return, max drawdown, and Sharpe-like ratio."""
    total_ret = (series.iloc[-1] / series.iloc[0] - 1) * 100
    days = (series.index[-1] - series.index[0]).days or 1
    ann_ret = ((series.iloc[-1] / series.iloc[0]) ** (365 / days) - 1) * 100

    # Max drawdown
    peak = series.cummax()
    drawdown = (series - peak) / peak * 100
    max_dd = drawdown.min()

    # Daily returns for volatility
    daily_ret = series.pct_change().dropna()
    volatility = daily_ret.std() * (252 ** 0.5) * 100 if len(daily_ret) > 1 else 0

    return {
        "total_return_pct": round(total_ret, 2),
        "annualized_pct": round(ann_ret, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "volatility_pct": round(volatility, 2),
        "start_val": round(series.iloc[0], 2),
        "end_val": round(series.iloc[-1], 2),
    }


def main():
    # Load bot portfolio
    portfolio = load_portfolio_daily(DB_PATH)
    if portfolio.empty:
        print("No portfolio data found in database.")
        return

    start_date = portfolio.index[0].strftime("%Y-%m-%d")
    end_date = (portfolio.index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    # Load benchmarks
    spy = load_benchmark("SPY", start_date, end_date)
    qqq = load_benchmark("QQQ", start_date, end_date)

    # Align all series to common dates
    all_dates = portfolio.index
    spy = spy.reindex(all_dates, method="ffill").dropna()
    qqq = qqq.reindex(all_dates, method="ffill").dropna()
    common = portfolio.index.intersection(spy.index).intersection(qqq.index)
    portfolio, spy, qqq = portfolio[common], spy[common], qqq[common]

    # Normalize to 100 for comparison
    port_norm = portfolio / portfolio.iloc[0] * 100
    spy_norm = spy / spy.iloc[0] * 100
    qqq_norm = qqq / qqq.iloc[0] * 100

    # Compute stats
    port_stats = compute_returns(portfolio)
    spy_stats = compute_returns(spy)
    qqq_stats = compute_returns(qqq)

    # --- Console Report ---
    print("=" * 65)
    print("  GPA TRADING BOT — PERFORMANCE REPORT")
    print(f"  Period: {start_date} to {portfolio.index[-1].strftime('%Y-%m-%d')} ({len(common)} trading days)")
    print("=" * 65)

    header = f"{'Metric':<22} {'Bot':>12} {'SPY':>12} {'QQQ':>12}"
    print(header)
    print("-" * 65)
    print(f"{'Start Value':<22} {'$'+str(port_stats['start_val']):>12} {'$'+str(spy_stats['start_val']):>12} {'$'+str(qqq_stats['start_val']):>12}")
    print(f"{'End Value':<22} {'$'+str(port_stats['end_val']):>12} {'$'+str(spy_stats['end_val']):>12} {'$'+str(qqq_stats['end_val']):>12}")
    print(f"{'Total Return':<22} {port_stats['total_return_pct']:>11.2f}% {spy_stats['total_return_pct']:>11.2f}% {qqq_stats['total_return_pct']:>11.2f}%")
    print(f"{'Annualized Return':<22} {port_stats['annualized_pct']:>11.2f}% {spy_stats['annualized_pct']:>11.2f}% {qqq_stats['annualized_pct']:>11.2f}%")
    print(f"{'Max Drawdown':<22} {port_stats['max_drawdown_pct']:>11.2f}% {spy_stats['max_drawdown_pct']:>11.2f}% {qqq_stats['max_drawdown_pct']:>11.2f}%")
    print(f"{'Volatility (ann.)':<22} {port_stats['volatility_pct']:>11.2f}% {spy_stats['volatility_pct']:>11.2f}% {qqq_stats['volatility_pct']:>11.2f}%")
    print("-" * 65)

    alpha_spy = port_stats["total_return_pct"] - spy_stats["total_return_pct"]
    alpha_qqq = port_stats["total_return_pct"] - qqq_stats["total_return_pct"]
    print(f"{'Alpha vs SPY':<22} {alpha_spy:>11.2f}%")
    print(f"{'Alpha vs QQQ':<22} {alpha_qqq:>11.2f}%")
    print("=" * 65)

    # --- Chart ---
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(port_norm.index, port_norm.values, label=f"Bot ({port_stats['total_return_pct']:+.1f}%)", linewidth=2)
    ax.plot(spy_norm.index, spy_norm.values, label=f"SPY ({spy_stats['total_return_pct']:+.1f}%)", linewidth=1.5, linestyle="--")
    ax.plot(qqq_norm.index, qqq_norm.values, label=f"QQQ ({qqq_stats['total_return_pct']:+.1f}%)", linewidth=1.5, linestyle="--")
    ax.axhline(100, color="gray", linewidth=0.5, linestyle=":")
    ax.set_title("GPA Trading Bot vs Market Benchmarks (Normalized to 100)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Value (indexed to 100)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    chart_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "performance_chart.png")
    fig.savefig(chart_path, dpi=150)
    print(f"\nChart saved to: {chart_path}")


if __name__ == "__main__":
    main()
