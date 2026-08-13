"""Reproducibility guards (SPEC.md §19, §22).

A forecast must be reconstructable, which requires that identical inputs
give bit-identical outputs. These tests pin the two places where
non-deterministic iteration order previously leaked into the MLE and
perturbed the optimiser at float precision.
"""

import subprocess
import sys

import pandas as pd

from bym.model import dixon_coles, priors

from test_dixon_coles import synthetic_matches
from test_priors import two_season_two_division


def test_arrivals_order_is_deterministic():
    m = two_season_two_division()
    keys = list(priors.arrivals(m, "E1", 2024).keys())
    assert keys == sorted(keys)


def test_fit_identical_across_prior_dict_orderings():
    """The same priors supplied in a different dict order must produce
    bit-identical parameters — otherwise run-to-run hash randomisation
    changes forecasts."""
    m = synthetic_matches(20)
    specs = {
        "Strong": priors.PriorSpec(-0.2, 0.05, 0.2, 0.05),
        "Weak": priors.PriorSpec(0.1, 0.05, -0.1, 0.05),
        "Middling A": priors.PriorSpec(0.0, 0.05, 0.0, 0.05),
    }
    forward = dixon_coles.fit(m, xi=0.0, param_priors=specs)
    reversed_specs = dict(reversed(list(specs.items())))
    backward = dixon_coles.fit(m, xi=0.0, param_priors=reversed_specs)

    for team in forward.alpha:
        assert forward.alpha[team] == backward.alpha[team]
        assert forward.beta[team] == backward.beta[team]
    assert forward.rho == backward.rho
    assert forward.log_likelihood == backward.log_likelihood


def test_arrivals_deterministic_across_hash_seeds():
    """Run the same computation in subprocesses with different
    PYTHONHASHSEED values — the historical source of the defect."""
    code = (
        "import sys; sys.path.insert(0, 'tests');"
        "from test_priors import two_season_two_division;"
        "from bym.model import priors;"
        "print(list(priors.arrivals(two_season_two_division(), 'E1', 2024).items()))"
    )
    outputs = set()
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stderr
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, f"non-deterministic across hash seeds: {outputs}"
