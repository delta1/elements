#!/bin/bash

DIR_UNIT_TEST_DATA=/home/byron/code/qa-assets/unit_test_data ./src/test/test_bitcoin -t script_tests/script_assets_test | tee src/test/script-assets-test-output.txt

