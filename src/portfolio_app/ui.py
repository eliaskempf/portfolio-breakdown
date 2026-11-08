"""Stable Streamlit script and render entrypoint."""
from portfolio_app.interface.ui import main, render_app

__all__ = ["main", "render_app"]


if __name__ == "__main__":
    main()
