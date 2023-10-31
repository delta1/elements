#!/usr/bin/env python3
# Copyright (c) 2016 The Bitcoin Core developers
# Distributed under the MIT/X11 software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

from decimal import Decimal
from test_framework.test_framework import BitcoinTestFramework
from test_framework.authproxy import JSONRPCException
from test_framework.util import (
    assert_equal,
)


class CTTest(BitcoinTestFramework):

    def set_test_params(self):
        self.num_nodes = 3
        self.setup_clean_chain = True
        args = [
            "-blindedaddresses=1",
            "-initialfreecoins=2100000000000000",
            "-con_blocksubsidy=0",
            "-con_connect_genesis_outputs=1",
            "-nctfeediscountfactor=10",
            "-minrelaytxfee=0.00000100",
        ]
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
        node0.generate(101)
        self.sync_all()
        balance = node0.getbalance()
        print(balance)
        assert_equal(balance['bitcoin'], 21000000)
        address = node0.getnewaddress()
        send = node0.getaddressinfo(address)
        send = node0.sendtoaddress(send['unconfidential'], 20999999, "", "", False, None, None, None, None, None, None, feerate)
        tx = node0.gettransaction(send, True, True)
        # print(tx)
        decoded = tx['decoded']
        vin = decoded['vin']
        vout = decoded['vout']
        node0.generate(1)
        print(f"vin: {len(vin)} vout: {len(vout)} fee: {tx['fee']}")

        for i in range(20):
            address = node1.getnewaddress()
            send = node1.getaddressinfo(address)
            send = node0.sendtoaddress(send['unconfidential'], 0.00000500, "", "", False, None, None, None, None, None, None, feerate)
            tx = node0.gettransaction(send, True, True)
            # print(tx)
            decoded = tx['decoded']
            vin = decoded['vin']
            vout = decoded['vout']
            node0.generate(1)
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
        self.nodes[0].generate(1)
        send = issued['txid']
        tx = self.nodes[0].gettransaction(send)
        print(f"fee: {tx['fee']}")
        print("---")

        for i in range(5):
            address = self.nodes[2].getnewaddress()
            send = self.nodes[2].getaddressinfo(address)
            # print(self.nodes[0].getbalance())
            send = self.nodes[0].sendtoaddress(send['address'], 1000, "", "", False, None, None, None, None, asset, None, feerate, True)
            print(f"send: {send}")
            txid = send['txid']
            print(f"txid: {send['txid']}")
            tx = self.nodes[0].gettransaction(txid, True, True)
            print(f"fee: {tx['fee']}")
            # temp = self.nodes[0].testmempoolaccept([tx['hex']])
            # print(f"temp: {temp}")
            decoded = tx['decoded']
            vin = decoded['vin']
            vout = decoded['vout']
            mempool = self.nodes[0].getmempoolinfo()
            print(f"mempool: {mempool}")
            raw = self.nodes[0].getrawmempool()
            print(f"raw: {raw}")

            self.nodes[0].generate(1)

            tx = self.nodes[0].gettransaction(txid, True, True)
            tx['hex'] = "snip"
            # print(tx)
            mempool = self.nodes[0].getmempoolinfo()
            print(f"mempool: {mempool}")
            raw = self.nodes[0].getrawmempool()
            print(f"raw: {raw}")
            assert tx['confirmations'] > 0
            print(f"vin: {len(vin)} vout: {len(vout)} size: {tx['decoded']['size']} fee: {tx['fee']}")

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
