#!/usr/bin/env bash

# set -euxo pipefail

for N in $(seq 1 100); do src/test/test_bitcoin --run_test=availablecoins_tests; done
