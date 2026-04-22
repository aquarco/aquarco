"""Build-type constant for Aquarco CLI.

In development builds this is ``"development"``.  The Homebrew formula
patches this file to ``"production"`` so that ``aquarco update`` is
disabled for public installs.
"""

import os
BUILD_TYPE: str = os.environ.get("AQUARCO_BUILD_TYPE", "development")
