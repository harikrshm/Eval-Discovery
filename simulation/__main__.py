"""``python -m simulation`` runs the same command as ``python -m simulation.run``."""

import sys

from simulation.run import main

sys.exit(main())
