#!/usr/bin/env python3
# Copyright (c) 2024 The Elements developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test ct() confidential-transaction descriptor support (ELIP-150).

Covers:
- A freshly created descriptor wallet derives its master blinding key from the
  HD seed via SLIP-0077 (dumpmasterblindingkey is non-empty and stable).
- listdescriptors emits ct(slip77(...), ...) wrappers.
- importdescriptors of a ct() export into a fresh wallet reproduces identical
  confidential addresses, can unblind received outputs, and can spend them.
"""

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_equal,
    assert_greater_than,
)


class WalletDescriptorCTTest(BitcoinTestFramework):
    def add_options(self, parser):
        self.add_wallet_options(parser, legacy=False)

    def set_test_params(self):
        self.num_nodes = 1
        self.setup_clean_chain = True
        args = [
            "-blindedaddresses=1",
            "-initialfreecoins=2100000000000000",
            "-con_blocksubsidy=0",
            "-con_connect_genesis_outputs=1",
            "-anyonecanspendaremine=1",
            "-fallbackfee=0.0001",
        ]
        self.extra_args = [args]

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()
        self.skip_if_no_sqlite()

    def run_test(self):
        node = self.nodes[0]

        # The default wallet holds the initial free coins (anyonecanspendaremine).
        funder = node.get_wallet_rpc(self.default_wallet_name)
        self.generate(node, 101, sync_fun=self.no_op)

        self.log.info("Create a descriptor wallet and confirm SLIP-0077 master blinding key")
        node.createwallet(wallet_name="ctw1", descriptors=True)
        w1 = node.get_wallet_rpc("ctw1")

        master_blind = w1.dumpmasterblindingkey()
        assert_equal(len(master_blind), 64)
        # Deterministic per-wallet: dumping again yields the same value.
        assert_equal(w1.dumpmasterblindingkey(), master_blind)

        self.log.info("Receive confidential funds")
        addr1 = w1.getnewaddress()
        info = w1.getaddressinfo(addr1)
        assert info["confidential"] != ""
        assert_equal(info["ismine"], True)

        funder.sendtoaddress(addr1, 10)
        self.generate(node, 1, sync_fun=self.no_op)

        bal1 = w1.getbalance()["bitcoin"]
        assert_equal(bal1, 10)
        # The received output is unblinded by the wallet.
        utxos = w1.listunspent()
        assert_greater_than(len(utxos), 0)
        assert any(u["amount"] == 10 for u in utxos)

        self.log.info("Export descriptors and verify ct(slip77(...)) wrappers are present")
        exported = w1.listdescriptors(True)["descriptors"]
        assert_greater_than(len(exported), 0)
        for d in exported:
            assert d["desc"].startswith("ct(slip77(%s)," % master_blind), d["desc"]

        # The public export must NOT embed the master blinding key (a secret).
        public = w1.listdescriptors()["descriptors"]
        for d in public:
            assert not d["desc"].startswith("ct(slip77("), d["desc"]
        # ... but it can be requested explicitly via include_blinding_key.
        public_blinded = w1.listdescriptors(False, True)["descriptors"]
        for d in public_blinded:
            assert d["desc"].startswith("ct(slip77(%s)," % master_blind), d["desc"]

        self.log.info("Import the export into a fresh blank wallet")
        node.createwallet(wallet_name="ctw2", descriptors=True, blank=True)
        w2 = node.get_wallet_rpc("ctw2")

        import_reqs = []
        for d in exported:
            req = {
                "desc": d["desc"],
                "timestamp": "now",
                "active": d.get("active", False),
            }
            if "range" in d:
                req["range"] = d["range"]
            if "internal" in d:
                req["internal"] = d["internal"]
            import_reqs.append(req)

        res = w2.importdescriptors(import_reqs)
        for r in res:
            assert_equal(r["success"], True)

        # The imported wallet must have adopted the same master blinding key.
        assert_equal(w2.dumpmasterblindingkey(), master_blind)

        self.log.info("Verify identical confidential address derivation")
        # Derive the same external addresses on both wallets and compare.
        for _ in range(3):
            a1 = w1.getnewaddress()
            a2 = w2.getnewaddress()
            assert_equal(a1, a2)

        self.log.info("Verify the imported wallet sees and unblinds the received output")
        w2.rescanblockchain()
        bal2 = w2.getbalance()["bitcoin"]
        assert_equal(bal2, 10)

        self.log.info("Verify the imported wallet can spend the received output")
        spend_target = funder.getnewaddress()
        txid = w2.sendtoaddress(spend_target, 1)
        assert txid in node.getrawmempool()
        self.generate(node, 1, sync_fun=self.no_op)
        # Balance decreased by at least the sent amount.
        assert_greater_than(10, w2.getbalance()["bitcoin"])


if __name__ == '__main__':
    WalletDescriptorCTTest(__file__).main()
