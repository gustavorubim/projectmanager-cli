# Test Harness

The tests in this directory exercise the major feature areas of the PM agent:

- `test_schema.py` checks the core `Task`/`ProjectPlan` models and serialization helpers.
- `test_analysis.py` validates scheduling analytics such as critical path, Monte Carlo risk, focus boards, and artifact inventory logic.
- `test_analytics.py` covers the historical snapshot pipeline and derived time-series metrics.
- `test_tools.py` verifies the LangGraph tool surface for working with project files, including plan persistence, logging, focus reports, and summaries.
- `conftest.py` provides fixtures that isolate every test inside a temporary project root while stubbing expensive visualization work.

## Running the tests

1. Install the project (and pytest if it is not already available):

   ```bash
   pip install -e .
   pip install pytest
   ```

2. Execute the full suite from the repository root:

   ```bash
   pytest
   ```

The fixtures automatically create and clean temporary directories, so the real project files remain untouched. Monte Carlo tests seed NumPy's RNG to keep the results deterministic.
