#!/usr/bin/env python3
# Copyright (c) 2024 The Elements Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""migratewallet preserves Elements confidential-transaction data.

Creates a legacy (BDB) wallet, receives confidential funds, then migrates it to a
descriptor wallet in place and asserts the Elements-specific invariants:
  - the master blinding key is carried over verbatim (not seed-derived),
  - the confidential balance survives and is still unblindable,
  - historical confidential addresses re-derive to the same blinding pubkey,
  - the migrated wallet can spend the previously-received confidential funds.
"""

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_equal,
    assert_greater_than,
)


class WalletLegacyMigrationTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 1
        self.extra_args = [["-deprecatedrpc=create_bdb"]]

    def add_options(self, parser):
        self.add_wallet_options(parser)

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()
        # Migration source is a legacy BDB wallet.
        self.skip_if_no_bdb()
        self.skip_if_no_sqlite()

    def run_test(self):
        node = self.nodes[0]

        # A funded default wallet to send confidential coins from.
        node.createwallet(wallet_name="funder", descriptors=False)
        funder = node.get_wallet_rpc("funder")
        node.get_wallet_rpc("funder")
        self.generatetoaddress(node, 101, funder.getnewaddress())

        self.log.info("Create a legacy wallet and receive confidential funds")
        node.createwallet(wallet_name="legacy", descriptors=False)
        legacy = node.get_wallet_rpc("legacy")
        assert_equal(legacy.getwalletinfo()["descriptors"], False)
        assert_equal(legacy.getwalletinfo()["format"], "bdb")

        master_blind = legacy.dumpmasterblindingkey()
        assert master_blind != "00" * 32

        # Receive to a confidential address.
        recv_addr = legacy.getnewaddress()
        recv_info = legacy.getaddressinfo(recv_addr)
        assert "confidential" in recv_info
        conf_addr = recv_info["confidential"]
        recv_blinding_pubkey = legacy.getaddressinfo(recv_addr)["confidential_key"]

        recv_txid = funder.sendtoaddress(conf_addr, 12)
        self.generatetoaddress(node, 1, funder.getnewaddress())
        assert_equal(legacy.getbalance()["bitcoin"], 12)
        # Confirm the received output is unblinded (amount known).
        assert_greater_than(legacy.getwalletinfo()["balance"]["bitcoin"], 0)

        # Capture the cached blinding factors of the received output so we can
        # verify they survive migration (blindingdata cached in the tx record).
        def receive_blinders(wallet):
            details = wallet.gettransaction(recv_txid, True, True)["details"]
            for d in details:
                if d["category"] == "receive" and d.get("amount") and abs(d["amount"]) == 12:
                    return (d.get("amountblinder"), d.get("assetblinder"))
            return (None, None)

        legacy_blinders = receive_blinders(legacy)
        assert legacy_blinders[0] is not None and legacy_blinders[1] is not None

        self.log.info("Migrate the legacy wallet to descriptors")
        res = node.migratewallet("legacy")
        assert_equal(res["wallet_name"], "legacy")
        migrated = node.get_wallet_rpc("legacy")
        assert_equal(migrated.getwalletinfo()["descriptors"], True)
        assert_equal(migrated.getwalletinfo()["format"], "sqlite")

        self.log.info("Master blinding key is preserved verbatim")
        assert_equal(migrated.dumpmasterblindingkey(), master_blind)

        self.log.info("listdescriptors export wraps in ct(slip77(<legacy master>))")
        exported = migrated.listdescriptors(True)["descriptors"]
        ct_descs = [d["desc"] for d in exported if d["desc"].startswith("ct(slip77(")]
        assert len(ct_descs) > 0, "migrated wallet did not export ct(slip77(...)) descriptors"
        for d in ct_descs:
            assert ("slip77(" + master_blind + ")") in d, d

        self.log.info("Confidential balance survives and stays unblindable")
        assert_equal(migrated.getbalance()["bitcoin"], 12)

        self.log.info("Cached output blinding factors (blindingdata) survive migration")
        assert_equal(receive_blinders(migrated), legacy_blinders)

        self.log.info("Historical confidential address re-derives the same blinding pubkey")
        migrated_info = migrated.getaddressinfo(recv_addr)
        assert_equal(migrated_info["ismine"], True)
        assert_equal(migrated_info["confidential_key"], recv_blinding_pubkey)
        assert_equal(migrated_info["confidential"], conf_addr)

        self.log.info("Migrated wallet can spend the previously-received confidential funds")
        dest = funder.getnewaddress()
        spend_txid = migrated.sendtoaddress(dest, 5)
        self.generatetoaddress(node, 1, funder.getnewaddress())
        assert_equal(migrated.gettransaction(spend_txid)["confirmations"], 1)


if __name__ == "__main__":
    WalletLegacyMigrationTest(__file__).main()
