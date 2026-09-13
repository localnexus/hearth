"""patch_data_root — relocate config_loader's data root for a test.

config_loader exposes the data root under two names (``_DATA``, ``DATA_DIR``);
patching only one leaves call sites that read the other pointing at the real
root. Any test that relocates the root must call this and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from hearth.config import config_loader
from hearth.session import maintenance_lock


def patch_data_root(case, root) -> Path:
    root = Path(root)
    for name in ("_DATA", "DATA_DIR"):
        patch = mock.patch.object(config_loader, name, root)
        patch.start()
        case.addCleanup(patch.stop)
    maintenance_lock._HELD.clear()
    case.addCleanup(lambda: [maintenance_lock.drop(c)
                             for c in list(maintenance_lock._HELD)])
    return root
