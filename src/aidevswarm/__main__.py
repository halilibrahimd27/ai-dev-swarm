"""``python -m aidevswarm`` entry point."""

from aidevswarm.orchestrator.orchestrator import main


def cli() -> None:
    main()


if __name__ == "__main__":
    cli()
