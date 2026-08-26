"""Pushpak — Real-time Airfare Price Index prototype (MoSPI DIID hackathon).

Component map (mirrors the eight-part brief):
  1. Data collection   -> pushpak.collect
  2. Normalization     -> pushpak.schema, pushpak.clean
  3. Index construction-> pushpak.index
  4. Dashboard         -> pushpak.web (served by pushpak.api)
  5. API               -> pushpak.api
  6. Validation        -> pushpak.validate
  7. Documentation     -> README.md
  8. Tests             -> tests/
"""
__version__ = "0.1.0"
