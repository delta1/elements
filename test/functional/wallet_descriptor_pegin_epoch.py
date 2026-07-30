#!/usr/bin/env python3
# Copyright (c) 2024 The Elements Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Prior-epoch peg-in claims for descriptor wallets.

A descriptor wallet gets a peg-in address under fedpegscript A and receives a
deposit. The sidechain then rotates its fedpegscript to B via dynafed. While A is
still within total_valid_epochs, the descriptor wallet must still be able to
claim and spend the deposit made under A.

This works without per-epoch pegin() ScriptPubKeyMans because the claim key is
the same wpkh key across epochs and createrawpegin matches the deposit against
every currently-valid fedpegscript (GetValidFedpegScripts).
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


class WalletDescriptorPeginEpochTest(BitcoinTestFramework):
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
            # Dynafed active from genesis, short epochs, keep 2 epochs valid.
            "-evbparams=dynafed:-1:::",
            "-dynamic_epoch_length=10",
            "-total_valid_epochs=2",
        ]
        self.add_nodes(1, [extra_args], chain=[parent_chain])
        self.start_node(1)

        self.log.info("Setting up network done")

    def run_test(self):
        self.import_deterministic_coinbase_privkeys()

        parent = self.nodes[0]
        sidechain = self.nodes[1]
        WSH_OP_TRUE = parent.decodescript("51")["segwit"]["hex"]

        parent.importprivkey(privkey=parent.get_deterministic_priv_key().key, label="mining")
        sidechain.importprivkey(privkey=sidechain.get_deterministic_priv_key().key, label="mining")
        util.node_fastmerkle = sidechain

        self.generate(parent, 101, sync_fun=self.no_op)
        self.generate(sidechain, 101, sync_fun=self.no_op)

        self.log.info("Get a peg-in address under the genesis fedpegscript (epoch A) and deposit")
        addrs = sidechain.getpeginaddress()
        mainchain_address = addrs["mainchain_address"]
        claim_script = addrs["claim_script"]
        fedpeg_a = sidechain.getsidechaininfo()["current_fedpegscripts"][0]

        txid = parent.sendtoaddress(mainchain_address, 24)
        find_vout_for_address(parent, txid, mainchain_address)
        self.generate(parent, 12, sync_fun=self.no_op)
        proof = parent.gettxoutproof([txid])
        raw = parent.gettransaction(txid)["hex"]

        self.log.info("Rotate the sidechain fedpegscript to B via dynafed (cross one epoch)")
        # Randomize the genesis fedpegscript into a valid different one.
        new_fedpegscript = sidechain.tweakfedpegscript("f00dbabe")["script"]
        assert new_fedpegscript != fedpeg_a

        # Propose fedpegscript B until it becomes an active current fedpegscript,
        # while keeping A within the total_valid_epochs window. With
        # dynamic_epoch_length=10 and total_valid_epochs=2, A remains valid for
        # roughly two epochs; stop as soon as B appears alongside A.
        current = sidechain.getsidechaininfo()["current_fedpegscripts"]
        for _ in range(40):
            if new_fedpegscript in current and fedpeg_a in current:
                break
            block_hex = sidechain.getnewblockhex(
                0,
                {
                    "signblockscript": WSH_OP_TRUE,
                    "max_block_witness": 10,
                    "fedpegscript": new_fedpegscript,
                    "extension_space": [],
                },
            )
            sidechain.submitblock(block_hex)
            current = sidechain.getsidechaininfo()["current_fedpegscripts"]

        self.log.info("current fedpegscripts after rotation: %s" % current)
        # B is now an active fedpegscript, but A must still be within the valid set.
        assert new_fedpegscript in current, "fedpegscript B did not activate"
        assert fedpeg_a in current, "epoch A fedpegscript fell out of the valid window too early"

        self.log.info("Claim the epoch-A deposit while B is active (descriptor wallet)")
        pegin_txid = sidechain.claimpegin(raw, proof, claim_script)
        self.generatetoaddress(sidechain, 1, sidechain.getnewaddress(), sync_fun=self.no_op)
        assert_equal(sidechain.gettransaction(pegin_txid)["confirmations"], 1)
        assert_greater_than(sidechain.getbalance()["bitcoin"], 23)

        self.log.info("Spend the pegged-in funds")
        spend_txid = sidechain.sendtoaddress(sidechain.getnewaddress(), 10)
        self.generatetoaddress(sidechain, 1, sidechain.getnewaddress(), sync_fun=self.no_op)
        assert_equal(sidechain.gettransaction(spend_txid)["confirmations"], 1)


if __name__ == "__main__":
    WalletDescriptorPeginEpochTest(__file__).main()
