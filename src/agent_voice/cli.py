import argparse

from agent_voice import __version__


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="agent-voice")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)
    if args.version:
        print(f"agent-voice {__version__}")
    return 0
