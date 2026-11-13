"""Bounded Telos shadow supervisor entry point; live modes require track L."""

from .supervisor import main

raise SystemExit(main())
