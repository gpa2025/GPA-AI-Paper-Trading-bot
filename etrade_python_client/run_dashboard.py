"""
Launch the AI Paper Trading Bot web dashboard.

Usage:
    python run_dashboard.py

Then open http://127.0.0.1:5000 in your browser.
"""

import sys
import logging
from logging.handlers import RotatingFileHandler

# Set up logging before importing anything else
logger = logging.getLogger("my_logger")
logger.setLevel(logging.DEBUG)
fh = RotatingFileHandler("python_client.log", maxBytes=5*1024*1024, backupCount=3)
fmt = logging.Formatter("%(asctime)-15s %(message)s", datefmt="%m/%d/%Y %I:%M:%S %p")
fh.setFormatter(fmt)
logger.addHandler(fh)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(fmt)
logger.addHandler(console)

from web.app import app

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"\n{'='*50}")
    print(f"  AI Paper Trading Bot — Web Dashboard")
    print(f"  Open http://127.0.0.1:{port} in your browser")
    print(f"{'='*50}\n")
    app.run(debug=False, host="127.0.0.1", port=port)
