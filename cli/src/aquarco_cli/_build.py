"""Build-type constant for Aquarco CLI.

In development builds this is ``"development"``.  The Homebrew formula
patches this file to ``"production"`` so that ``aquarco update`` is
disabled for public installs.
"""

BUILD_TYPE: str = "development"
