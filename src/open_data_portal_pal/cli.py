"""Console script for open_data_portal_pal."""

import typer
from rich.console import Console

from open_data_portal_pal import utils

app = typer.Typer()
console = Console()


@app.command()
def main() -> None:
    """Console script for open_data_portal_pal."""
    console.print("Replace this message by putting your code into open_data_portal_pal.cli.main")
    console.print("See Typer documentation at https://typer.tiangolo.com/")
    utils.do_something_useful()


if __name__ == "__main__":
    app()
