import json

import numpy as np
import pytest

from trackyard.lanes import MODEL_PATH, TreeEnsemble, export_gbc, load_models


@pytest.mark.parametrize("n_classes", [2, 3])
def test_exported_trees_match_scikit_learn(n_classes):
    ensemble = pytest.importorskip("sklearn.ensemble")
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, 6))
    y = np.array(["a", "b", "c"])[(x[:, 0] > 0).astype(int) + (n_classes == 3) * (x[:, 1] > 1).astype(int)]
    model = ensemble.GradientBoostingClassifier(n_estimators=20, max_depth=3, random_state=0).fit(x, y)
    ours = TreeEnsemble(json.loads(json.dumps(export_gbc(model))))
    test = rng.normal(size=(100, 6))
    assert np.allclose(ours.predict_proba(test), model.predict_proba(test), atol=1e-9)
    assert ours.predict(test) == list(model.predict(test))


def test_shipped_models_load():
    if not MODEL_PATH.exists():
        pytest.skip("no trained lane models")
    models = load_models()
    assert set(models) == {"cymbal", "tom"}
    assert set(models["cymbal"].classes) <= {"yellow-cym", "blue-cym", "green-cym"}
    assert set(models["tom"].classes) == {"yellow-tom", "blue-tom", "green-tom"}
