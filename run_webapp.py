# -*- coding: utf-8 -*-
"""
Startup script for the Solid Edge Similarity Analyzer web application.
"""

import sys
import os
from pathlib import Path

# Set working directory to script path (fix for IDE execution).
os.chdir(Path(__file__).parent)

# UTF-8 encoding fix on Windows before imports.
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
try:
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# Add project directory to import path.
sys.path.insert(0, str(Path(__file__).parent))

from webapp.app import run_server

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Solid Edge Similarity Web App")
    parser.add_argument('--host', default='127.0.0.1', help='Host address')
    parser.add_argument('--port', type=int, default=5000, help='Port number')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')

    args = parser.parse_args()

    print("""
    ============================================================
     SOLID EDGE SIMILARITY ANALYZER - WEB INTERFACE
    ============================================================

     Open browser at: http://{}:{}

     Press Ctrl+C to stop
    ============================================================
    """.format(args.host, args.port))

    run_server(host=args.host, port=args.port, debug=args.debug)

