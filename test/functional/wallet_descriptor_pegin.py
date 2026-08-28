#!/usr/bin/env python3
# Copyright (c) 2024 The Elements Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Peg-in support for descriptor wallets.

Exercises the core peg-in round-trip against a descriptor sidechain wallet:
  getpeginaddress -> deposit on the parent chain -> claimpegin -> the pegged-in
  output is IsMine and spendable.

This asserts descriptor wallets are no longer rejected by getpeginaddress and
that the resulting bech32 claim output is owned by the wallet's active wpkh
descriptor (i.e. no legacy pegin-specific bookkeeping is required for a basic
peg-in).
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


class WalletDescriptorPeginTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 2

    def add_options(self, parser):
        self.add_wallet_options(parser)

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

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

    def run_test(self):
        self.import_deterministic_coinbase_privkeys()

        parent = self.nodes[0]
        sidechain = self.nodes[1]

        parent.importprivkey(privkey=parent.get_deterministic_priv_key().key, label="mining")
        sidechain.importprivkey(privkey=sidechain.get_deterministic_priv_key().key, label="mining")
        util.node_fastmerkle = sidechain

        self.generate(parent, 101, sync_fun=self.no_op)
        self.generate(sidechain, 101, sync_fun=self.no_op)

        self.log.info("getpeginaddress works for a descriptor wallet")
        addrs = sidechain.getpeginaddress()
        mainchain_address = addrs["mainchain_address"]
        claim_script = addrs["claim_script"]
        # The claim script must be a native v0 witness key hash (wpkh), which the
        # descriptor wallet owns via its active wpkh descriptor.
        assert_equal(sidechain.decodescript(claim_script)["type"], "witness_v0_keyhash")

        # The mainchain deposit address is the tweaked-contract P2WSH (or its
        # P2SH-wrapped form), matching calculate_contract.
        current_fedpegscript = sidechain.getsidechaininfo()["current_fedpegscripts"][0]
        tweaked = sidechain.tweakfedpegscript(claim_script, current_fedpegscript)
        if sidechain.getaddressinfo(mainchain_address)["iswitness"]:
            assert_equal(tweaked["p2wsh"], mainchain_address)
        else:
            assert_equal(tweaked["p2shwsh"], mainchain_address)

        self.log.info("Deposit to the mainchain address and claim the peg-in")
        deposit_amount = 24
        txid = parent.sendtoaddress(mainchain_address, deposit_amount)
        find_vout_for_address(parent, txid, mainchain_address)
        # 10 + 2 confirmations required to get into mempool and confirm.
        self.generate(parent, 12, sync_fun=self.no_op)
        proof = parent.gettxoutproof([txid])
        raw = parent.gettransaction(txid)["hex"]

        # The descriptor wallet must be able to sign the peg-in input, whose
        # prevout (the claim script) is not in the wallet's coins map.
        raw_pegin = sidechain.createrawpegin(raw, proof, claim_script)["hex"]
        signed = sidechain.signrawtransactionwithwallet(raw_pegin)
        assert_equal(signed["complete"], True)

        # Claim via the high-level RPC too (also exercises wallet-driven signing).
        pegin_txid = sidechain.claimpegin(raw, proof)
        self.generate(sidechain, 1, sync_fun=self.no_op)

        self.log.info("The pegged-in output is IsMine and increases wallet balance")
        gettx = sidechain.gettransaction(pegin_txid)
        assert_equal(gettx["confirmations"], 1)
        # The wallet should now hold approximately the deposit amount in bitcoin.
        bal = sidechain.getbalance()["bitcoin"]
        assert_greater_than(bal, deposit_amount - 1)

        self.log.info("Spend the pegged-in output to a fresh wallet address")
        dest = sidechain.getnewaddress()
        spend_txid = sidechain.sendtoaddress(dest, 10)
        self.generate(sidechain, 1, sync_fun=self.no_op)
        spend_tx = sidechain.gettransaction(spend_txid)
        assert_equal(spend_tx["confirmations"], 1)

        # The peg-in claim output was created by the wallet's wpkh descriptor and
        # is recognized as owned, confirming no legacy script store is required.
        decoded = sidechain.decodescript(claim_script)
        claim_address = decoded.get("address") or decoded["addresses"][0]
        assert_equal(sidechain.getaddressinfo(claim_address)["ismine"], True)


if __name__ == "__main__":
    WalletDescriptorPeginTest(__file__).main()
