#!/usr/bin/env python3
# Copyright (c) 2017-2020 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Tests that issueasset correctly honors the "blind" argument.

See: https://github.com/ElementsProject/elements/issues/1312
"""
from decimal import Decimal

from test_framework.blocktools import COINBASE_MATURITY
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_equal,
)

class WalletTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 2
        self.extra_args = [[
            "-blindedaddresses=1",
            "-initialfreecoins=2100000000000000",
            "-con_blocksubsidy=0",
            "-con_connect_genesis_outputs=1",
            "-txindex=1",
        ]] * self.num_nodes
        self.extra_args[0].append("-anyonecanspendaremine=1")

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def run_test(self):
        self.generate(self.nodes[0], COINBASE_MATURITY + 1)
        self.sync_all()

        addr1 = self.nodes[1].getnewaddress()
        print(addr1)
        self.nodes[0].sendtoaddress(addr1, 10000000) # 1
        self.generate(self.nodes[0], 1)
        print(self.nodes[0].getbalance())
        print(self.nodes[1].getbalance())

        self.log.info(f"Sending an explicit unblinded tx")
        addr = self.nodes[1].getnewaddress("", "bech32")
        print(addr)
        txid = self.nodes[0].sendtoaddress(addr, 100) # 4
        self.generate(self.nodes[0], 1)
        # print(self.nodes[0].getbalance())
        self.log.info(f"txid is {txid}")
        tx = self.nodes[0].gettransaction(txid)
        tx['hex'] = '<snip>'
        print(tx)

        self.log.info(f"Sending a blinded confidential tx")
        addr = self.nodes[1].getnewaddress("", "blech32")
        print(addr)
        txid = self.nodes[0].sendtoaddress(addr, 100) # 5
        self.generate(self.nodes[0], 1)
        # print(self.nodes[0].getbalance())
        self.log.info(f"txid is {txid}")
        tx = self.nodes[0].gettransaction(txid)
        tx['hex'] = '<snip>'
        print(tx)

        self.log.info(f"Issuing an asset with blind=true")
        issuance = self.nodes[0].issueasset(100, 1, True) # 2
        print(issuance)
        self.generate(self.nodes[0], 1)
        # print(self.nodes[0].getbalance())
        asset = issuance["asset"]
        self.log.info(f"Asset ID is {asset}")
        txid = issuance["txid"]
        self.log.info(f"txid is {txid}")
        tx = self.nodes[0].gettransaction(txid)
        tx['hex'] = '<snip>'
        print(tx)

        self.log.info(f"Issuing an asset with blind=false")
        issuance = self.nodes[0].issueasset(100, 1, False) # 3
        print(issuance)
        self.generate(self.nodes[0], 1)
        # print(self.nodes[0].getbalance())
        asset = issuance["asset"]
        self.log.info(f"Asset ID is {asset}")
        txid = issuance["txid"]
        self.log.info(f"txid is {txid}")
        tx = self.nodes[0].gettransaction(txid)
        tx['hex'] = '<snip>'
        print(tx)
        for d in tx['details']:
            print(f"amount blinder: {d['amountblinder']}")
            print(f"asset blinder: {d['assetblinder']}")
            # assert_equal(d['amountblinder'], "0000000000000000000000000000000000000000000000000000000000000000")
            # assert_equal(d['assetblinder'], "0000000000000000000000000000000000000000000000000000000000000000")

        print(f"node0 balance: {self.nodes[0].getbalance()}")
        print(f"node1 balance: {self.nodes[1].getbalance()}")

        self.log.info(f"Reissue asset")
        issuance = self.nodes[0].reissueasset(asset, 200) # 3
        print(issuance)
        self.generate(self.nodes[0], 1)
        # print(self.nodes[0].getbalance())
        txid = issuance["txid"]
        self.log.info(f"txid is {txid}")
        tx = self.nodes[0].gettransaction(txid)
        tx['hex'] = '<snip>'
        print(tx)
        for d in tx['details']:
            print(f"amount blinder: {d['amountblinder']}")
            print(f"asset blinder: {d['assetblinder']}")
            # assert_equal(d['amountblinder'], "0000000000000000000000000000000000000000000000000000000000000000")
            # assert_equal(d['assetblinder'], "0000000000000000000000000000000000000000000000000000000000000000")

        print(f"node0 balance: {self.nodes[0].getbalance()}")
        print(f"node1 balance: {self.nodes[1].getbalance()}")

        # assert False

if __name__ == '__main__':
    WalletTest().main()
