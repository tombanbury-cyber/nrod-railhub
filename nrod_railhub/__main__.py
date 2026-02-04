#!/usr/bin/env python3
"""Entry point for nrod_railhub package."""

from .cli import parse_args, connect_and_run
from .logging_config import setup_logger


def main():
    args = parse_args()
    
    # If --verbose flag is set and log-level wasn't explicitly changed from default,
    # automatically set log level to verbose
    if args.verbose and args.log_level == "error":
        args.log_level = "verbose"
    
    # Setup logging based on command-line argument
    setup_logger(args.log_level)
    
    if not args.pretty and not args.raw:
        args.pretty = True
    connect_and_run(args)


if __name__ == "__main__":
    main()
