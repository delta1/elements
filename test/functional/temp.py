#!/usr/bin/env python3
# Copyright (c) 2016 The Bitcoin Core developers
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

import time

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
        args = ["-blindedaddresses=1", "-initialfreecoins=2100000000000000", "-con_blocksubsidy=0", "-con_connect_genesis_outputs=1", "-nctfeediscountfactor=10"]
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

        node0 = self.nodes[0]
        node1 = self.nodes[1]
        node2 = self.nodes[2]

        # coinbase_addr = self.nodes[0].getnewaddress()
        # node0.generatetoaddress(201, coinbase_addr)
        self.generate(node0, 101)
        self.sync_all()
        balance = node0.getbalance()
        print(balance)
        assert_equal(balance['bitcoin'], 21000000)
        address = node0.getnewaddress()
        info = node0.getaddressinfo(address)
        txid = node0.sendtoaddress(info['unconfidential'], 20999999, "", "", False, None, None, None, None, None, None, feerate)
        tx = node0.gettransaction(txid, True, True)
        # print(tx)
        decoded = tx['decoded']
        vin = decoded['vin']
        vout = decoded['vout']
        self.generate(node0, 1)
        print(f"vin: {len(vin)} vout: {len(vout)} fee: {tx['fee']}")

        for i in range(20):
            address = node1.getnewaddress()
            info = node1.getaddressinfo(address)
            txid = node0.sendtoaddress(info['unconfidential'], 0.00000500, "", "", False, None, None, None, None, None, None, feerate)
            tx = node0.gettransaction(txid, True, True)
            # print(tx)
            decoded = tx['decoded']
            vin = decoded['vin']
            vout = decoded['vout']
            self.generate(node0, 1)
            print(f"vin: {len(vin)} vout: {len(vout)} fee: {tx['fee']}")

        unspent = node1.listunspent()
        # print(unspent)
        # assert_equal(len(unspent), 5)

        print("---")
        print("issue asset")
        issued = self.nodes[0].issueasset(5000, 1, False)
        # print(issued)
        asset = issued['asset']
        print(f"asset: {asset}")
        self.generate(node0, 1)
        txid = issued['txid']
        tx = self.nodes[0].gettransaction(txid)
        print(f"fee: {tx['fee']}")
        print("---")

        for i in range(5):
            address = self.nodes[2].getnewaddress()
            info = self.nodes[2].getaddressinfo(address)
            print(self.nodes[0].getbalance())
            txid = self.nodes[0].sendtoaddress(info['address'], 1000, "", "", False, None, None, None, None, asset, None, feerate)
            tx = self.nodes[0].gettransaction(txid, True, True)
            print(f"fee: {tx['fee']}")
            decoded = tx['decoded']
            vin = decoded['vin']
            vout = decoded['vout']
            self.generate(node0, 1, sync_fun=self.no_op)
            tx = self.nodes[0].gettransaction(txid, True, True)
            tx['hex'] = "snip"
            print(f"tx: {tx}")
            mempool = self.nodes[0].getmempoolinfo()
            print(f"mempool: {mempool}")
            raw = self.nodes[0].getrawmempool()
            print(f"raw: {raw}")
            assert tx['confirmations'] > 0
            print(f"vin: {len(vin)} vout: {len(vout)} size: {tx['decoded']['vsize']} fee: {tx['fee']}")

        unspent = self.nodes[2].listunspent()
        # print(unspent)
        assert_equal(len(unspent), 5)

        # address = self.nodes[1].getnewaddress()
        # info = self.nodes[1].getaddressinfo(address)
        # # print("send btc")
        # txid = self.nodes[0].sendtoaddress(info['unconfidential'], 1000, "", "", False, None, None, None, None, None, None, feerate)
        # print(txid)
        # print(tx)
        # self.sync_all()

        # for amount in range(10, 0, -1):
        #     (from_node,to_node) = (self.nodes[0], self.nodes[1]) if amount % 2 == 0 else (self.nodes[1], self.nodes[0])

        #     address = to_node.getnewaddress()
        #     info = to_node.getaddressinfo(address)
        #     # print(info)
        #     # print(f"send {amount}")
        #     txid = from_node.sendtoaddress(info['unconfidential'], amount, "", "", False, None, None, None, None, None, None, feerate)
        #     # print(txid)
        #     tx = from_node.gettransaction(txid)
        #     # print(tx)
        #     print(f"fee: {tx['fee']}")
        #     from_node.generate(1)
        #     self.sync_all()
        #     balance = self.nodes[0].getbalance()
        #     # print(balance)
        #     balance = self.nodes[1].getbalance()
        #     # print(balance)

        # print("issue asset")
        # issued = self.nodes[0].issueasset(100, 1, False)
        # print(issued)
        # asset = issued['asset']
        # print(f"asset: {asset}")
        # self.nodes[0].generate(1)
        # self.sync_all()
        # time.sleep(1)
        # txid = issued['txid']
        # tx = self.nodes[0].gettransaction(txid)
        # print(f"fee: {tx['fee']}")

        # balance = self.nodes[0].getbalance()
        # print(balance)

        # for amount in range(10, 0, -1):
        #     (from_node,to_node) = (self.nodes[0], self.nodes[1]) if amount % 2 == 0 else (self.nodes[1], self.nodes[0])

        #     address = to_node.getnewaddress()
        #     info = to_node.getaddressinfo(address)
        #     # print(info)
        #     print(f"send {amount} of asset")
        #     txid = from_node.sendtoaddress(info['address'], amount, "", "", False, None, None, None, None, asset, None, feerate)
        #     # print(txid)
        #     tx = from_node.gettransaction(txid)
        #     # print(tx)
        #     print(f"fee: {tx['fee']}")
        #     from_node.generate(1)
        #     self.sync_all()
        #     balance = self.nodes[0].getbalance()
        #     # print(balance)
        #     balance = self.nodes[1].getbalance()
        #     # print(balance)

        # height0 = self.nodes[0].getblockchaininfo()['blocks']
        # height1 = self.nodes[1].getblockchaininfo()['blocks']
        # height2 = self.nodes[2].getblockchaininfo()['blocks']

        # assert height0 == height1 == height2

        # assert False

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
