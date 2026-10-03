"""Separate experimental executable; production entrypoint stays unchanged."""
# Redirect absent windowed streams before importing the application or GUI stack.
from portfolio_app.window import main

if __name__ == '__main__':
    main()
