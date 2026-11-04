import importlib

import pytest


@pytest.mark.parametrize("module", ["trackyard.pipeline", "trackyard.cli", "trackyard.editor.server", "trackyard.lanes"])
def test_modules_import(module):
    importlib.import_module(module)
