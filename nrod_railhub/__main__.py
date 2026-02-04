#!/usr/bin/env python3
"""Entry point for nrod_railhub package."""

from .cli import parse_args, connect_and_run
from .logging_config import setup_logger


def main():
    args = parse_args()
    
    # Setup logging based on command-line argument
    setup_logger(args.log_level)
    
    if not args.pretty and not args.raw:
        args.pretty = True
    connect_and_run(args)


if __name__ == "__main__":
    main()
