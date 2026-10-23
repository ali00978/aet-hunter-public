# AET Hunter Public — development tests

This is a clean public development repository containing reviewed scanner source and synthetic offline tests only. **It is not the AET CARS production system.**

Commercial scanning, credentials, candidate lists, saved results, favorites, and AET Pro database/sync stay in the [private AET CARS project](https://github.com/ali00978/aet-cars-ai-hunter) and its secured infrastructure.

Public GitHub Actions run offline development regression tests, **not** recurring live commercial data collection.

Python 3.12: `python run_offline_tests.py`
