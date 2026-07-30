#!/usr/bin/env python3
# Copyright (c) 2024 The Elements Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Backup/restore of a descriptor wallet across a peg-in.

A descriptor wallet's peg-in claim keys come from its ordinary wpkh descriptor,
so a listdescriptors -> importdescriptors round-trip is a complete backup: a
restored wallet re-derives the same peg-in claim address, sees and can spend the
pegged-in output. A public (watch-only) restore recognizes the output but cannot
spend it.
"""

from test_framework.test_framework import BitcoinTestFramework
from test_framework import util
from test_framework.util import (
    assert_equal,
    assert_greater_than,
    assert_raises_rpc_error,
    get_auth_cookie,
    get_datadir_path,
    find_vout_for_address,
    p2p_port,
    rpc_port,
)


class WalletDescriptorBackupRestoreTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 2

    def add_options(self, parser):
        self.add_wallet_options(parser)

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()
        # This test is descriptor-specific.
        self.skip_if_no_sqlite()

    def setup_network(self, split=False):
        self.nodes = []
        parent_chain = "elementsregtest"

        # Parent node (node 0): an elements chain acting as the mainchain.
        extra_args = [
            "-port=" + str(p2p_port(0)),
            "-rpcport=" + str(rpc_port(0)),
            "-validatepegin=0",
            "-initialfreecoins=0",
            "-anyonecanspendaremine=1",
            "-signblockscript=51",  # OP_TRUE
        ]
        self.add_nodes(1, [extra_args], chain=[parent_chain])
        self.start_node(0)

        self.parentgenesisblockhash = self.nodes[0].getblockhash(0)
        parent_pegged_asset = self.nodes[0].getsidechaininfo()["pegged_asset"]

        # Sidechain node (node 1): validates peg-ins against node 0.
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
        ]
        self.add_nodes(1, [extra_args], chain=[parent_chain])
        self.start_node(1)

        self.log.info("Setting up network done")

    def do_pegin(self, parent, sidechain, wallet):
        """Peg in to `wallet` (an RPC handle) and return (claim_script, pegin_txid)."""
        addrs = wallet.getpeginaddress()
        mainchain_address = addrs["mainchain_address"]
        claim_script = addrs["claim_script"]
        txid = parent.sendtoaddress(mainchain_address, 24)
        find_vout_for_address(parent, txid, mainchain_address)
        self.generate(parent, 12, sync_fun=self.no_op)
        proof = parent.gettxoutproof([txid])
        raw = parent.gettransaction(txid)["hex"]
        pegin_txid = wallet.claimpegin(raw, proof)
        self.generate(sidechain, 1, sync_fun=self.no_op)
        return claim_script, pegin_txid

    def run_test(self):
        self.import_deterministic_coinbase_privkeys()

        parent = self.nodes[0]
        sidechain = self.nodes[1]

        parent.importprivkey(privkey=parent.get_deterministic_priv_key().key, label="mining")
        sidechain.importprivkey(privkey=sidechain.get_deterministic_priv_key().key, label="mining")
        util.node_fastmerkle = sidechain

        self.generate(parent, 101, sync_fun=self.no_op)
        self.generate(sidechain, 101, sync_fun=self.no_op)

        # Original wallet is the default wallet on the sidechain node.
        origin = sidechain.get_wallet_rpc(self.default_wallet_name)

        self.log.info("Peg in to the original descriptor wallet")
        claim_script, pegin_txid = self.do_pegin(parent, sidechain, origin)
        claim_addr = self.claim_address(origin, claim_script)
        assert_equal(origin.getaddressinfo(claim_addr)["ismine"], True)
        assert_greater_than(origin.getbalance()["bitcoin"], 23)

        self.log.info("Export descriptors (private) and import into a fresh wallet")
        exported = origin.listdescriptors(True)["descriptors"]
        # The claim key lives in the wallet's ordinary wpkh descriptor(s), so the
        # export is a full backup of the peg-in claim keys.
        assert any(d["desc"].startswith("ct(") or "wpkh(" in d["desc"] for d in exported)

        sidechain.createwallet(wallet_name="restored", blank=True, descriptors=True)
        restored = sidechain.get_wallet_rpc("restored")
        import_reqs = []
        for d in exported:
            req = {"desc": d["desc"], "timestamp": 0}
            if "range" in d:
                req["range"] = d["range"]
            if "active" in d:
                req["active"] = d["active"]
            if "internal" in d:
                req["internal"] = d["internal"]
            import_reqs.append(req)
        res = restored.importdescriptors(import_reqs)
        for r in res:
            assert r["success"], r

        self.log.info("Restored wallet re-derives the same claim address and owns the peg-in")
        assert_equal(restored.getaddressinfo(claim_addr)["ismine"], True)
        # It sees and unblinds the pegged-in output received by the original.
        restored.rescanblockchain()
        assert_greater_than(restored.getbalance()["bitcoin"], 23)

        self.log.info("Restored wallet can spend the pegged-in funds")
        dest = origin.getnewaddress()
        spend_txid = restored.sendtoaddress(dest, 10)
        self.generate(sidechain, 1, sync_fun=self.no_op)
        assert_equal(restored.gettransaction(spend_txid)["confirmations"], 1)

        self.log.info("Public (watch-only) restore recognizes the output but cannot spend")
        pub_exported = origin.listdescriptors(False)["descriptors"]
        sidechain.createwallet(wallet_name="watch", blank=True, descriptors=True, disable_private_keys=True)
        watch = sidechain.get_wallet_rpc("watch")
        watch_reqs = []
        for d in pub_exported:
            req = {"desc": d["desc"], "timestamp": 0}
            if "range" in d:
                req["range"] = d["range"]
            watch_reqs.append(req)
        wres = watch.importdescriptors(watch_reqs)
        for r in wres:
            assert r["success"], r
        watch.rescanblockchain()
        assert_equal(watch.getaddressinfo(claim_addr)["ismine"], True)
        assert_equal(watch.getaddressinfo(claim_addr)["solvable"], True)
        # No private keys: spending must fail.
        assert_raises_rpc_error(-4, "", watch.sendtoaddress, origin.getnewaddress(), 1)

    @staticmethod
    def claim_address(wallet, claim_script):
        decoded = wallet.decodescript(claim_script)
        return decoded.get("address") or decoded["addresses"][0]


if __name__ == "__main__":
    WalletDescriptorBackupRestoreTest(__file__).main()
