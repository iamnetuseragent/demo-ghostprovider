"""Entry point for `demo_ghostprovider` command and `python -m demo_ghostprovider`."""

import logging


def run() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
    )
    from demo_ghostprovider.paths import migrate_legacy_data
    migrate_legacy_data()
    from demo_ghostprovider.app import GhostProviderApp
    app = GhostProviderApp()
    app.run()


if __name__ == "__main__":
    run()
