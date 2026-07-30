#!/usr/bin/env python3
# Copyright (c) 2024 The Elements Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Peg-in claim survives legacy -> descriptor migration.

A legacy wallet gets a peg-in address, receives a deposit on the parent chain,
and is then migrated to a descriptor wallet. The migrated wallet must still own
the peg-in claim script (the claim key becomes an ordinary migrated wpkh
descriptor) and be able to claim and spend the pegged-in funds.
"""

from test_framework.test_framework import BitcoinTestFramework
from test_framework import util
from test_framework.util import (
    assert_equal,
    assert_greater_than,
    get_auth_cookie,
    get_datadir_path,
    find_vout_for_address,
    p2p_port,
    rpc_port,
)


class WalletPeginMigrationTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 2

    def add_options(self, parser):
        self.add_wallet_options(parser)

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()
        self.skip_if_no_bdb()
        self.skip_if_no_sqlite()

    def setup_network(self, split=False):
        self.nodes = []
        parent_chain = "elementsregtest"

        extra_args = [
            "-port=" + str(p2p_port(0)),
            "-rpcport=" + str(rpc_port(0)),
            "-validatepegin=0",
            "-initialfreecoins=0",
            "-anyonecanspendaremine=1",
            "-signblockscript=51",
        ]
        self.add_nodes(1, [extra_args], chain=[parent_chain])
        self.start_node(0)

        self.parentgenesisblockhash = self.nodes[0].getblockhash(0)
        parent_pegged_asset = self.nodes[0].getsidechaininfo()["pegged_asset"]

        self.fedpegscript = "512103dff4923d778550cc13ce0d887d737553b4b58f4e8e886507fc39f5e447b2186451ae"
        datadir = get_datadir_path(self.options.tmpdir, 0)
        rpc_u, rpc_p = get_auth_cookie(datadir, parent_chain)
        extra_args = [
            "-printtoconsole=0",
            "-port=" + str(p2p_port(1)),
            "-rpcport=" + str(rpc_port(1)),
            "-validatepegin=1",
            "-fedpegscript=%s" % self.fedpegscript,
            "-minrelaytxfee=0",
            "-blockmintxfee=0",
            "-initialfreecoins=0",
            "-peginconfirmationdepth=10",
            "-mainchainrpchost=127.0.0.1",
            "-mainchainrpcport=%s" % rpc_port(0),
            "-mainchainrpcuser=%s" % rpc_u,
            "-mainchainrpcpassword=%s" % rpc_p,
            "-parentgenesisblockhash=%s" % self.parentgenesisblockhash,
            "-parentpubkeyprefix=235",
            "-parentscriptprefix=75",
            "-parent_bech32_hrp=ert",
            "-con_parent_chain_signblockscript=51",
            "-con_parent_pegged_asset=%s" % parent_pegged_asset,
            "-checkmempool=0",
            "-deprecatedrpc=create_bdb",
        ]
        self.add_nodes(1, [extra_args], chain=[parent_chain])
        self.start_node(1)

        self.log.info("Setting up network done")

    def run_test(self):
        self.import_deterministic_coinbase_privkeys()

        parent = self.nodes[0]
        sidechain = self.nodes[1]

        parent.importprivkey(privkey=parent.get_deterministic_priv_key().key, label="mining")
        sidechain.importprivkey(privkey=sidechain.get_deterministic_priv_key().key, label="mining")
        util.node_fastmerkle = sidechain

        self.generate(parent, 101, sync_fun=self.no_op)
        self.generate(sidechain, 101, sync_fun=self.no_op)

        self.log.info("Create a legacy sidechain wallet and get a peg-in address")
        sidechain.createwallet(wallet_name="legacy", descriptors=False)
        legacy = sidechain.get_wallet_rpc("legacy")
        assert_equal(legacy.getwalletinfo()["descriptors"], False)

        addrs = legacy.getpeginaddress()
        mainchain_address = addrs["mainchain_address"]
        claim_script = addrs["claim_script"]
        claim_addr = self.claim_address(legacy, claim_script)
        assert_equal(legacy.getaddressinfo(claim_addr)["ismine"], True)

        self.log.info("Deposit on the parent chain (peg-in pending on the legacy wallet)")
        txid = parent.sendtoaddress(mainchain_address, 24)
        find_vout_for_address(parent, txid, mainchain_address)
        self.generate(parent, 12, sync_fun=self.no_op)
        proof = parent.gettxoutproof([txid])
        raw = parent.gettransaction(txid)["hex"]

        self.log.info("Migrate the legacy wallet to descriptors before claiming")
        sidechain.migratewallet("legacy")
        migrated = sidechain.get_wallet_rpc("legacy")
        assert_equal(migrated.getwalletinfo()["descriptors"], True)

        self.log.info("Migrated wallet still owns the peg-in claim script")
        assert_equal(migrated.getaddressinfo(claim_addr)["ismine"], True)

        self.log.info("Migrated (descriptor) wallet can claim and spend the peg-in")
        pegin_txid = migrated.claimpegin(raw, proof, claim_script)
        self.generate(sidechain, 1, sync_fun=self.no_op)
        assert_equal(migrated.gettransaction(pegin_txid)["confirmations"], 1)
        assert_greater_than(migrated.getbalance()["bitcoin"], 23)

        dest = migrated.getnewaddress()
        spend_txid = migrated.sendtoaddress(dest, 10)
        self.generate(sidechain, 1, sync_fun=self.no_op)
        assert_equal(migrated.gettransaction(spend_txid)["confirmations"], 1)

    @staticmethod
    def claim_address(wallet, claim_script):
        decoded = wallet.decodescript(claim_script)
        return decoded.get("address") or decoded["addresses"][0]


if __name__ == "__main__":
    WalletPeginMigrationTest(__file__).main()
