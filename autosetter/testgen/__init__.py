"""
autosetter.testgen
==================
Constraint-driven test generation with the Z3 SMT solver.

The text model describes the problem's input as a JSON test spec
(`test_spec.json`); this package turns the spec into input files:

- `spec`     loads and validates the spec
- `engine`   solves the integer variables with Z3, one strategy per seed
- `builders` builds arrays, strings, permutations, matrices, trees, graphs
- `layout`   renders input text, parses it back, and checks it (including
             the problem's official samples)
"""

from autosetter.testgen.builders import TestGenError
from autosetter.testgen.engine import PLAN, GeneratedTest, generate_test, strategy_for
from autosetter.testgen.layout import LayoutError, check_input, check_samples
from autosetter.testgen.spec import SpecError, TestSpec, load_spec, load_spec_file

__all__ = [
    "PLAN",
    "GeneratedTest",
    "LayoutError",
    "SpecError",
    "TestGenError",
    "TestSpec",
    "check_input",
    "check_samples",
    "generate_test",
    "load_spec",
    "load_spec_file",
    "strategy_for",
]
