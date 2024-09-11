#!/usr/bin/env python3

from decimal import Decimal
from time import sleep
from test_framework.messages import COIN
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_raises_rpc_error,
    find_vout_for_address,
    get_auth_cookie,
    get_datadir_path,
    rpc_port,
    p2p_port,
    assert_equal,
)
from test_framework import util


def get_new_unconfidential_address(node, addr_type="p2sh-segwit"):
    addr = node.getnewaddress("", addr_type)
    val_addr = node.getaddressinfo(addr)
    if "unconfidential" in val_addr:
        return val_addr["unconfidential"]
    return val_addr["address"]


class PeginSubsidyTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 4

    def add_options(self, parser):
        parser.add_argument(
            "--parent_binpath",
            dest="parent_binpath",
            default="",
            help="Use a different binary for launching nodes",
        )
        parser.add_argument(
            "--parent_bitcoin",
            dest="parent_bitcoin",
            default=False,
            action="store_true",
            help="Parent nodes are Bitcoin",
        )
        parser.add_argument(
            "--pre_transition",
            dest="pre_transition",
            default=False,
            action="store_true",
            help="Run test in dynafed activated chain, without a transition",
        )
        parser.add_argument(
            "--post_transition",
            dest="post_transition",
            default=False,
            action="store_true",
            help="Run test in dynafed activated chain, after transition and additional epoch to invalidate old fedpegscript",
        )

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def setup_network(self, split=False):
        if self.options.parent_bitcoin and self.options.parent_binpath == "":
            raise Exception(
                "Can't run with --parent_bitcoin without specifying --parent_binpath"
            )

        self.nodes = []
        # Setup parent nodes
        parent_chain = (
            "elementsregtest" if not self.options.parent_bitcoin else "regtest"
        )
        parent_binary = (
            [self.options.parent_binpath] if self.options.parent_binpath != "" else None
        )
        for n in range(2):
            extra_args = [
                "-port=" + str(p2p_port(n)),
                "-rpcport=" + str(rpc_port(n)),
                # "-txindex=1",
            ]
            if self.options.parent_bitcoin:
                # bitcoind can't read elements.conf config files
                extra_args.extend(
                    [
                        "-regtest=1",
                        "-printtoconsole=0",
                        "-server=1",
                        "-discover=0",
                        "-keypool=1",
                        "-listenonion=0",
                        "-addresstype=legacy",  # To make sure bitcoind gives back p2pkh no matter version
                        "-fallbackfee=0.0002",
                    ]
                )
            else:
                extra_args.extend(
                    [
                        "-validatepegin=0",
                        "-initialfreecoins=0",
                        "-anyonecanspendaremine=1",
                        "-signblockscript=51",  # OP_TRUE
                    ]
                )

            self.add_nodes(1, [extra_args], chain=[parent_chain], binary=parent_binary)
            self.start_node(n)
            print("Node {} started".format(n))
        # set hard-coded mining keys for non-Elements chains
        if self.options.parent_bitcoin:
            self.nodes[0].set_deterministic_priv_key(
                "2Mysp7FKKe52eoC2JmU46irt1dt58TpCvhQ",
                "cTNbtVJmhx75RXomhYWSZAafuNNNKPd1cr2ZiUcAeukLNGrHWjvJ",
            )
            self.nodes[1].set_deterministic_priv_key(
                "2N19ZHF3nEzBXzkaZ3N5sVBJXQ8jZ7Udpg5",
                "cRnDSw1JsjmYYEN6xxQvf5pqMENsRE584z6MdWfJ7v85c4ciitkk",
            )

        self.connect_nodes(0, 1)
        self.parentgenesisblockhash = self.nodes[0].getblockhash(0)
        if not self.options.parent_bitcoin:
            parent_pegged_asset = self.nodes[0].getsidechaininfo()["pegged_asset"]

        # Setup sidechain nodes
        self.fedpeg_script = (
            "512103dff4923d778550cc13ce0d887d737553b4b58f4e8e886507fc39f5e447b2186451ae"
        )
        for n in range(2):
            validatepegin = "1" if n == 0 else "0"
            extra_args = [
                "-printtoconsole=0",
                "-port=" + str(p2p_port(2 + n)),
                "-rpcport=" + str(rpc_port(2 + n)),
                "-validatepegin=%s" % validatepegin,
                "-fallbackfee=0.00001000",
                "-fedpegscript=%s" % self.fedpeg_script,
                "-minrelaytxfee=0",
                "-blockmintxfee=0",
                "-initialfreecoins=0",
                "-peginconfirmationdepth=10",
                "-mainchainrpchost=127.0.0.1",
                "-mainchainrpcport=%s" % rpc_port(n),
                "-parentgenesisblockhash=%s" % self.parentgenesisblockhash,
                "-parentpubkeyprefix=111",
                "-parentscriptprefix=196",
                "-parent_bech32_hrp=bcrt",
                # Turn of consistency checks that can cause assert when parent node stops
                # and a peg-in transaction fails this belt-and-suspenders check.
                # NOTE: This can cause spurious problems in regtest, and should be dealt with in a better way.
                "-checkmempool=0",
                "-peginsubsidyheight=110",
                "-peginsubsidythreshold=2.0",
            ]
            if not self.options.parent_bitcoin:
                extra_args.extend(
                    [
                        "-parentpubkeyprefix=235",
                        "-parentscriptprefix=75",
                        "-parent_bech32_hrp=ert",
                        "-con_parent_chain_signblockscript=51",
                        "-con_parent_pegged_asset=%s" % parent_pegged_asset,
                    ]
                )

            # Immediate activation of dynafed when requested versus "never" from conf
            if self.options.pre_transition or self.options.post_transition:
                extra_args.extend(["-evbparams=dynafed:-1:::"])

            # Use rpcuser auth only for first parent.
            if n == 0:
                # Extract username and password from cookie file and use directly.
                datadir = get_datadir_path(self.options.tmpdir, n)
                rpc_u, rpc_p = get_auth_cookie(datadir, parent_chain)
                extra_args.extend(
                    [
                        "-mainchainrpcuser=%s" % rpc_u,
                        "-mainchainrpcpassword=%s" % rpc_p,
                    ]
                )
            else:
                # Need to specify where to find parent cookie file
                datadir = get_datadir_path(self.options.tmpdir, n)
                extra_args.append(
                    "-mainchainrpccookiefile="
                    + datadir
                    + "/"
                    + parent_chain
                    + "/.cookie"
                )

            self.add_nodes(1, [extra_args], chain=["elementsregtest"])
            self.start_node(2 + n)
            print("Node {} started".format(2 + n))

        # We only connect the same-chain nodes, so sync_all works correctly
        self.connect_nodes(2, 3)
        self.node_groups = [
            [self.nodes[0], self.nodes[1]],
            [self.nodes[2], self.nodes[3]],
        ]
        for node_group in self.node_groups:
            self.sync_all(node_group)
        print("Setting up network done")

    def run_test(self):
        self.import_deterministic_coinbase_privkeys()  # Create wallets for all nodes

        parent = self.nodes[0]
        # parent2 = self.nodes[1]
        sidechain = self.nodes[2]
        sidechain2 = self.nodes[3]

        assert_equal(
            sidechain.getsidechaininfo()["pegin_confirmation_depth"], 10
        )  # 10+2 confirms required to get into mempool and confirm

        parent.importprivkey(
            privkey=parent.get_deterministic_priv_key().key, label="mining"
        )
        sidechain.importprivkey(
            privkey=sidechain.get_deterministic_priv_key().key, label="mining"
        )
        util.node_fastmerkle = sidechain

        self.generate(parent, 101, sync_fun=self.no_op)
        self.generate(sidechain, 101, sync_fun=self.no_op)

        def sync_fun():
            return sleep(0.25)

        self.log.info(
            "createrawpegin before enforcement height with validatepegin below threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 101)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_fun)
        # assert False

        self.log.info(
            "createrawpegin before enforcement height with validatepegin above threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 102)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "createrawpegin before enforcement height without validatepegin below threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 103)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegintx = sidechain2.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "createrawpegin before enforcement height without validatepegin above threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 104)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegintx = sidechain2.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin before enforcement height with validatepegin below threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 105)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin before enforcement height with validatepegin above threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 106)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin before enforcement height without validatepegin below threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 107)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin before enforcement height without validatepegin above threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 108)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info("===== enforcement")
        num = 110 - sidechain.getblockchaininfo()["blocks"]
        self.generate(sidechain, num, sync_fun=self.no_op)

        self.log.info(
            "createrawpegin after enforcement height with validatepegin below threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 110)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        vsize = pegin_tx["decoded"]["vsize"]
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "createrawpegin after enforcement height with validatepegin above threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 111)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 3.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "createrawpegin after enforcement height without validatepegin below threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 112)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied because validatepegin is off.",
            sidechain2.createrawpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        pegintx = sidechain2.createrawpegin(
            bitcoin_txhex, txoutproof, claim_script, feerate
        )
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        vsize = pegin_tx["decoded"]["vsize"]
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        assert pegin_tx["decoded"]["vout"][1]["value"] * COIN >= feerate * vsize
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "createrawpegin after enforcement height without validatepegin above threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 113)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied because validatepegin is off.",
            sidechain2.createrawpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        pegintx = sidechain2.createrawpegin(
            bitcoin_txhex, txoutproof, claim_script, feerate
        )
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin after enforcement height with validatepegin below threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 114)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        vsize = pegin_tx["decoded"]["vsize"]
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        # assert_greater_than_or_equal(pegin_tx["decoded"]["vout"][1]["value"] * COIN, feerate * vsize)
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin after enforcement height with validatepegin above threshold"
        )
        assert_equal(sidechain.getblockchaininfo()["blocks"], 115)
        address = sidechain.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin after enforcement height without validatepegin below threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 116)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied because validatepegin is off.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        pegin_txid = sidechain2.claimpegin(
            bitcoin_txhex, txoutproof, claim_script, feerate
        )
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        vsize = pegin_tx["decoded"]["vsize"]
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        assert pegin_tx["decoded"]["vout"][1]["value"] * COIN >= feerate * vsize
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin after enforcement height without validatepegin above threshold"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 117)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 2.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied because validatepegin is off.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        pegin_txid = sidechain2.claimpegin(
            bitcoin_txhex, txoutproof, claim_script, feerate
        )
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        self.log.info(
            "claimpegin after enforcement height without validatepegin below threshold,"
        )
        self.log.info("but without or incorrect subsidy output")
        self.log.info(
            "should be accepted by the node without validatepegin, but rejected by the node with validatepegin=1"
        )
        assert_equal(sidechain2.getblockchaininfo()["blocks"], 118)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.0
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        # print(f"maintxid: {maintxid}")
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied because validatepegin is off.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        low_feerate = 1.0
        pegin_txid = sidechain2.claimpegin(
            bitcoin_txhex, txoutproof, claim_script, low_feerate
        )
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        # print(pegin_tx)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        # print(pegin_tx["decoded"]["vout"][1]["value"] * COIN)
        # print(feerate * vsize)
        # print(low_feerate * vsize)
        # assert_equal(pegin_tx["decoded"]["vout"][1]["value"] * COIN, 355)

        assert_equal(
            sidechain.testmempoolaccept([pegin_tx["hex"]])[0]["allowed"], False
        )
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        assert_equal(sidechain2.getblockchaininfo()["blocks"], 119)
        address = sidechain2.getpeginaddress()
        mainchain_address, claim_script = (
            address["mainchain_address"],
            address["claim_script"],
        )
        assert_equal(
            sidechain2.decodescript(address["claim_script"])["type"],
            "witness_v0_keyhash",
        )
        feerate = 2.0
        amount = 1.99999999
        maintxid = parent.sendtoaddress(
            address=mainchain_address, amount=amount, fee_rate=feerate
        )
        # print(f"maintxid: {maintxid}")
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof = parent.gettxoutproof([maintxid])
        bitcoin_txhex = parent.gettransaction(maintxid)["hex"]
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied because validatepegin is off.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        low_feerate = 1.0
        pegin_txid = sidechain2.claimpegin(
            bitcoin_txhex, txoutproof, claim_script, low_feerate
        )
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        # print(pegin_tx)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        # print(pegin_tx["decoded"]["vout"][1]["value"] * COIN)
        # print(feerate * vsize)
        # print(low_feerate * vsize)
        # assert_equal(pegin_tx["decoded"]["vout"][1]["value"] * COIN, 355)

        # print(sidechain.testmempoolaccept([pegin_tx['hex']]))
        assert_equal(
            sidechain.testmempoolaccept([pegin_tx["hex"]])[0]["allowed"], False
        )
        self.generate(sidechain2, 1, sync_fun=sync_fun)

        # =================================

        self.log.info("construct a multi-pegin tx, below threshold")
        assert_equal(sidechain.getblockchaininfo()["blocks"], 120)
        address = sidechain.getpeginaddress()
        mainchain_address1, claim_script1 = (
            address["mainchain_address"],
            address["claim_script"],
        )
        feerate = 2.0
        amount1 = 0.5
        txid1 = parent.sendtoaddress(
            address=mainchain_address1, amount=amount1, fee_rate=feerate
        )
        vout1 = find_vout_for_address(parent, txid1, mainchain_address1)
        address = sidechain.getpeginaddress()
        mainchain_address2, claim_script2 = (
            address["mainchain_address"],
            address["claim_script"],
        )
        feerate = 2.0
        amount2 = 1.0
        txid2 = parent.sendtoaddress(
            address=mainchain_address2, amount=amount2, fee_rate=feerate
        )
        vout2 = find_vout_for_address(parent, txid2, mainchain_address2)
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof1 = parent.gettxoutproof([txid1])
        txoutproof2 = parent.gettxoutproof([txid2])
        bitcoin_txhex1 = parent.gettransaction(txid1)["hex"]
        bitcoin_txhex2 = parent.gettransaction(txid2)["hex"]

        pegintx1 = sidechain.createrawpegin(bitcoin_txhex1, txoutproof1, claim_script1)
        pegintx2 = sidechain.createrawpegin(bitcoin_txhex2, txoutproof2, claim_script2)
        decoded1 = sidechain.decoderawtransaction(pegintx1["hex"])
        decoded2 = sidechain.decoderawtransaction(pegintx2["hex"])
        print(f"pegintx1: {pegintx1}")
        print(f"decoded1: {decoded1}")
        print(f"pegintx2: {pegintx2}")
        print(f"decoded2: {decoded2}")

        addr1 = get_new_unconfidential_address(sidechain)
        addr2 = get_new_unconfidential_address(sidechain)

        inputs = [
            {
                "txid": txid1,
                "vout": vout1,
                "pegin_bitcoin_tx": bitcoin_txhex1,
                "pegin_txout_proof": txoutproof1,
                "pegin_claim_script": claim_script1,
            },
            {
                "txid": txid2,
                "vout": vout2,
                "pegin_bitcoin_tx": bitcoin_txhex2,
                "pegin_txout_proof": txoutproof2,
                "pegin_claim_script": claim_script2,
            },
        ]
        fee = Decimal("0.00000363")
        subsidy = Decimal("0.00001267")
        outputs = [
            {addr1: Decimal("0.5") - fee - subsidy},
            {addr2: 1.0},
            {"burn": subsidy},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        signed = sidechain.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-subsidy-too-low")

        subsidy = Decimal("0.00001310")
        outputs = [
            {addr1: Decimal("0.5") - fee - subsidy},
            {addr2: 1.0},
            {"burn": subsidy},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        signed = sidechain.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        print(signed)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        print(accept)
        assert_equal(accept[0]["allowed"], True)
        txid = sidechain.sendrawtransaction(signed["hex"])
        print(txid)
        self.generate(sidechain2, 1, sync_fun=sync_fun)
        print(sidechain.gettransaction(txid))

        self.log.info("construct a multi-pegin tx, above threshold")
        assert_equal(sidechain.getblockchaininfo()["blocks"], 121)
        address = sidechain.getpeginaddress()
        mainchain_address1, claim_script1 = (
            address["mainchain_address"],
            address["claim_script"],
        )
        feerate = 1.0
        amount1 = 0.5
        txid1 = parent.sendtoaddress(
            address=mainchain_address1, amount=amount1, fee_rate=feerate
        )
        vout1 = find_vout_for_address(parent, txid1, mainchain_address1)
        address = sidechain.getpeginaddress()
        mainchain_address2, claim_script2 = (
            address["mainchain_address"],
            address["claim_script"],
        )
        feerate = 2.0
        amount2 = 1.5
        txid2 = parent.sendtoaddress(
            address=mainchain_address2, amount=amount2, fee_rate=feerate
        )
        vout2 = find_vout_for_address(parent, txid2, mainchain_address2)
        self.generate(parent, 12, sync_fun=self.no_op)
        txoutproof1 = parent.gettxoutproof([txid1])
        txoutproof2 = parent.gettxoutproof([txid2])
        bitcoin_txhex1 = parent.gettransaction(txid1)["hex"]
        bitcoin_txhex2 = parent.gettransaction(txid2)["hex"]

        pegintx1 = sidechain.createrawpegin(bitcoin_txhex1, txoutproof1, claim_script1)
        pegintx2 = sidechain.createrawpegin(bitcoin_txhex2, txoutproof2, claim_script2)
        decoded1 = sidechain.decoderawtransaction(pegintx1["hex"])
        decoded2 = sidechain.decoderawtransaction(pegintx2["hex"])
        print(f"pegintx1: {pegintx1}")
        print(f"decoded1: {decoded1}")
        print(f"pegintx2: {pegintx2}")
        print(f"decoded2: {decoded2}")

        addr1 = get_new_unconfidential_address(sidechain)
        addr2 = get_new_unconfidential_address(sidechain)

        inputs = [
            {
                "txid": txid1,
                "vout": vout1,
                "pegin_bitcoin_tx": bitcoin_txhex1,
                "pegin_txout_proof": txoutproof1,
                "pegin_claim_script": claim_script1,
            },
            {
                "txid": txid2,
                "vout": vout2,
                "pegin_bitcoin_tx": bitcoin_txhex2,
                "pegin_txout_proof": txoutproof2,
                "pegin_claim_script": claim_script2,
            },
        ]
        fee = Decimal("0.00000363")
        outputs = [
            {addr1: 0.5},
            {addr2: Decimal('1.5') - fee},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        signed = sidechain.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        print(accept)
        assert_equal(accept[0]["allowed"], True)
        txid = sidechain.sendrawtransaction(signed["hex"])
        print(txid)
        self.generate(sidechain2, 1, sync_fun=sync_fun)
        print(sidechain.gettransaction(txid))

        # Manually stop sidechains first, then the parent chains.
        self.stop_node(2)
        self.stop_node(3)
        self.stop_node(0)
        self.stop_node(1)


if __name__ == "__main__":
    PeginSubsidyTest().main()
