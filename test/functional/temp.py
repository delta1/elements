#!/usr/bin/env python3
# Copyright (c) 2016 The Bitcoin Core developers
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

import io

from decimal import Decimal
from test_framework.test_framework import BitcoinTestFramework
from test_framework.authproxy import JSONRPCException
from test_framework.messages import (
    COIN,
    CTransaction,
    CTxOut,
    CTxOutAsset,
    CTxOutValue,
    CTxInWitness,
    CTxOutWitness,
    tx_from_hex,
)
from test_framework.util import (
    assert_equal,
    hex_str_to_bytes,
    BITCOIN_ASSET_OUT,
    assert_raises_rpc_error,
)
import os
import re

from test_framework.liquid_addr import (
    encode,
    decode,
)

class CTTest(BitcoinTestFramework):

    def set_test_params(self):
        self.num_nodes = 3
        self.setup_clean_chain = True
        args = ["-blindedaddresses=1", "-initialfreecoins=2100000000000000", "-con_blocksubsidy=0", "-con_connect_genesis_outputs=1"]
        self.extra_args = [args] * self.num_nodes
        self.extra_args[0].append("-anyonecanspendaremine=1") # first node gets the coins

    def setup_network(self, split=False):
        self.setup_nodes()
        self.connect_nodes(0, 1)
        self.connect_nodes(1, 2)
        self.connect_nodes(0, 2)
        self.sync_all()

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def run_test(self):

        feerate = 1.0

        # Running balances
        balance = self.nodes[0].getbalance()
        print(balance)
        assert_equal(balance['bitcoin'], 21000000)

        self.nodes[0].generate(101)
        self.sync_all()

        address = self.nodes[1].getnewaddress()
        info = self.nodes[1].getaddressinfo(address)
        print(info)
        txid = self.nodes[0].sendtoaddress(info['unconfidential'], 100, "", "", False, None, None, None, None, None, None, feerate)
        # print(txid)
        tx = self.nodes[0].gettransaction(txid)
        print(tx)
        print(f"fee: {tx['fee']}")
        self.nodes[0].generate(201)
        self.sync_all()
        balance = self.nodes[0].getbalance()
        print(balance)
        tx = self.nodes[0].gettransaction(txid)
        print(tx)

        address = self.nodes[1].getnewaddress()
        info = self.nodes[1].getaddressinfo(address)
        print(info)
        txid = self.nodes[0].sendtoaddress(info['unconfidential'], 100, "", "", False, None, None, None, None, None, None, feerate)
        # print(txid)
        tx = self.nodes[0].gettransaction(txid)
        print(tx)
        print(f"fee: {tx['fee']}")
        self.nodes[0].generate(201)
        self.sync_all()
        balance = self.nodes[0].getbalance()
        print(balance)

        address = self.nodes[1].getnewaddress()
        info = self.nodes[1].getaddressinfo(address)
        print(info)
        txid = self.nodes[0].sendtoaddress(info['address'], 100, "", "", False, None, None, None, None, None, None, feerate)
        # print(txid)
        tx = self.nodes[0].gettransaction(txid)
        print(tx)
        print(f"fee: {tx['fee']}")
        self.nodes[0].generate(101)
        self.sync_all()
        balance = self.nodes[0].getbalance()
        print(balance)

        # # Unblinded issuance of asset
        issued = self.nodes[0].issueasset(100, 1, False)
        # print(issued)
        asset = issued['asset']
        # print(asset)
        self.nodes[0].reissueasset(asset, 100)
        self.nodes[0].generate(1)
        # self.sync_all()

        balance = self.nodes[0].getbalance()
        # print(balance)

        address = self.nodes[1].getnewaddress()
        print("address")
        print(address)
        info = self.nodes[1].getaddressinfo(address)
        print("info")
        print(info)

        txid = self.nodes[0].sendtoaddress(address, 10, "", "", False, None, None, None, None, asset, None, feerate)
        # print(txid)
        tx = self.nodes[0].gettransaction(txid)
        # print(tx)
        print(f"fee: {tx['fee']}")
        self.nodes[0].generate(1)
        self.sync_all()

        # issuancedata = self.nodes[2].issueasset(0, Decimal('0.00000006')) #0 of asset, 6 reissuance token

        # # Node 2 will send node 1 a reissuance token, both will generate assets
        # self.nodes[2].sendtoaddress(self.nodes[1].getnewaddress(), Decimal('0.00000001'), "", "", False, False, 1, "UNSET", False, issuancedata["token"])
        # # node 1 needs to know about a (re)issuance to reissue itself
        # self.nodes[1].importaddress(self.nodes[2].gettransaction(issuancedata["txid"])["details"][0]["address"])
        # # also send some bitcoin
        # self.nodes[2].generate(1)
        # self.sync_all()

        # self.nodes[1].reissueasset(issuancedata["asset"], Decimal('0.05'))
        # self.nodes[2].reissueasset(issuancedata["asset"], Decimal('0.025'))
        # self.nodes[1].generate(1)
        # self.sync_all()


if __name__ == '__main__':
    CTTest().main()
