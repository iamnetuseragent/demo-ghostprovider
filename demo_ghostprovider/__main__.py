"""Entry point for `demo-ghostprovider` command and `python -m demo_ghostprovider`."""

def run() -> None:
    from demo_ghostprovider.app import DemoGhostProviderApp
    app = DemoGhostProviderApp()
    app.run()


if __name__ == "__main__":
    run()
