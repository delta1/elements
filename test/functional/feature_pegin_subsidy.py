#!/usr/bin/env python3

# can be run with parent bitcoind node
# tested with bitcoind v28.2 and v29.0
# test/functional/feature_pegin_subsidy.py --parent_bitcoin --parent_binpath="/path/to/bitcoind" --nosandbox

from decimal import Decimal
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

PEGIN_SUBSIDY_HEIGHT = 150


def get_new_unconfidential_address(node, addr_type="p2sh-segwit"):
    addr = node.getnewaddress("", addr_type)
    val_addr = node.getaddressinfo(addr)
    if "unconfidential" in val_addr:
        return val_addr["unconfidential"]
    return val_addr["address"]


class PeginSubsidyTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 3
        self.disable_syscall_sandbox = True

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
            raise Exception("Can't run with --parent_bitcoin without specifying --parent_binpath")

        self.nodes = []
        # Setup parent nodes
        parent_chain = "elementsregtest" if not self.options.parent_bitcoin else "regtest"
        parent_binary = [self.options.parent_binpath] if self.options.parent_binpath != "" else None

        extra_args = [
            "-port=" + str(p2p_port(0)),
            "-rpcport=" + str(rpc_port(0)),
            # to test minimum parent tx fee
            "-minrelaytxfee=0.00000100",
            "-blockmintxfee=0.00000100",
            "-mintxfee=0.00000100",
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
                    "-deprecatedrpc=create_bdb",
                ]
            )
            self.expected_stderr = (
                f"Error: Unable to bind to 127.0.0.1:{p2p_port(1)} on this computer. Elements Core is probably already running."
            )
        else:
            extra_args.extend(
                [
                    "-validatepegin=0",
                    "-initialfreecoins=0",
                    "-anyonecanspendaremine=1",
                    "-signblockscript=51",  # OP_TRUE
                    "-dustrelayfee=0.00003000",  # use the Bitcoin default dust relay fee rate for the parent nodes
                ]
            )
            self.expected_stderr = ""

        self.add_nodes(1, [extra_args], chain=[parent_chain], binary=parent_binary)
        self.start_node(0)
        self.log.info(f"Node 0 started (mainchain: {'bitcoind' if self.options.parent_bitcoin else 'elementsd'})")

        # set hard-coded mining keys for non-Elements chains
        if self.options.parent_bitcoin:
            self.nodes[0].set_deterministic_priv_key(
                "2Mysp7FKKe52eoC2JmU46irt1dt58TpCvhQ",
                "cTNbtVJmhx75RXomhYWSZAafuNNNKPd1cr2ZiUcAeukLNGrHWjvJ",
            )

        self.parentgenesisblockhash = self.nodes[0].getblockhash(0)
        if not self.options.parent_bitcoin:
            parent_pegged_asset = self.nodes[0].getsidechaininfo()["pegged_asset"]

        # Setup sidechain nodes
        self.fedpeg_script = "512103dff4923d778550cc13ce0d887d737553b4b58f4e8e886507fc39f5e447b2186451ae"
        for n in range(2):
            validatepegin = "1" if n == 0 else "0"
            extra_args = [
                "-printtoconsole=0",
                "-port=" + str(p2p_port(1 + n)),
                "-rpcport=" + str(rpc_port(1 + n)),
                "-validatepegin=%s" % validatepegin,
                "-fallbackfee=0.00001000",
                "-fedpegscript=%s" % self.fedpeg_script,
                "-minrelaytxfee=0",
                "-blockmintxfee=0",
                "-initialfreecoins=0",
                "-peginconfirmationdepth=10",
                "-mainchainrpchost=127.0.0.1",
                "-mainchainrpcport=%s" % rpc_port(0),
                "-parentgenesisblockhash=%s" % self.parentgenesisblockhash,
                "-parentpubkeyprefix=111",
                "-parentscriptprefix=196",
                "-parent_bech32_hrp=bcrt",
                # Turn of consistency checks that can cause assert when parent node stops
                # and a peg-in transaction fails this belt-and-suspenders check.
                # NOTE: This can cause spurious problems in regtest, and should be dealt with in a better way.
                "-checkmempool=0",
                "-peginsubsidyheight=%s" % PEGIN_SUBSIDY_HEIGHT,
                "-peginsubsidythreshold=2.0",
                "-peginminamount=1.0",
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
                extra_args.append("-mainchainrpccookiefile=" + datadir + "/" + parent_chain + "/.cookie")

            self.add_nodes(1, [extra_args], chain=["elementsregtest"])
            self.start_node(1 + n)
            self.log.info(f"Node {1 + n} started (sidechain: elementsd)")

        # We only connect the same-chain nodes, so sync_all works correctly
        self.connect_nodes(1, 2)
        self.node_groups = [
            [self.nodes[0]],
            [self.nodes[1], self.nodes[2]],
        ]
        for node_group in self.node_groups:
            self.sync_all(node_group)
        self.log.info("Setting up network done")

    def run_test(self):
        self.import_deterministic_coinbase_privkeys()  # Create wallets for all nodes

        parent = self.nodes[0]
        sidechain = self.nodes[1]
        sidechain2 = self.nodes[2]

        assert_equal(sidechain.getsidechaininfo()["pegin_confirmation_depth"], 10)  # 10+2 confirms required to get into mempool and confirm

        parent.importprivkey(privkey=parent.get_deterministic_priv_key().key, label="mining")
        sidechain.importprivkey(privkey=sidechain.get_deterministic_priv_key().key, label="mining")
        util.node_fastmerkle = sidechain

        self.generate(parent, 101, sync_fun=self.no_op)
        self.generate(sidechain, 101, sync_fun=self.no_op)

        def sync_sidechain():
            return self.sync_all([sidechain, sidechain2])

        DEFAULT_FEERATE = 1.0

        def parent_pegin(parent, node, amount=1.0, feerate=DEFAULT_FEERATE):
            address = node.getpeginaddress()
            mainchain_address, claim_script = (
                address["mainchain_address"],
                address["claim_script"],
            )
            txid = parent.sendtoaddress(address=mainchain_address, amount=amount, fee_rate=feerate)
            vout = find_vout_for_address(parent, txid, mainchain_address)
            self.generate(parent, 12, sync_fun=self.no_op)
            txoutproof = parent.gettxoutproof([txid])
            tx = parent.gettransaction(txid)
            bitcoin_txhex = tx["hex"]
            # tx fee result is negative so negate it
            fee = -tx["fee"] if self.options.parent_bitcoin else -tx["fee"]["bitcoin"]
            return (
                txid,
                vout,
                txoutproof,
                bitcoin_txhex,
                claim_script,
                fee,
            )

        self.log.info("check new fields for getpeginaddress and getsidechaininfo")
        result = sidechain.getpeginaddress()
        assert_equal(result["pegin_min_amount"], "1.00")
        assert_equal(result["pegin_subsidy_threshold"], "2.00")
        assert_equal(result["pegin_subsidy_height"], PEGIN_SUBSIDY_HEIGHT)
        assert_equal(result["pegin_subsidy_active"], False)
        result = sidechain.getsidechaininfo()
        assert_equal(result["pegin_min_amount"], "1.00")
        assert_equal(result["pegin_subsidy_threshold"], "2.00")
        assert_equal(result["pegin_subsidy_height"], PEGIN_SUBSIDY_HEIGHT)
        assert_equal(result["pegin_subsidy_active"], False)

        self.log.info("createrawpegin before enforcement, with validatepegin, below threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=1.0, feerate=2.0)
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("createrawpegin before enforcement, with validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=2.0, feerate=2.0)
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("createrawpegin before enforcement, without validatepegin, below threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=1.0, feerate=2.0)
        pegintx = sidechain2.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("createrawpegin before enforcement, without validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=2.0, feerate=2.0)
        pegintx = sidechain2.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin before enforcement, with validatepegin, below threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=1.0, feerate=2.0)
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin before enforcement, with validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=2.0, feerate=2.0)
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin before enforcement, without validatepegin, below threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=1.0, feerate=2.0)
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin before enforcement, without validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=2.0, feerate=2.0)
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("check min pegin amount before subsidy height")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=0.5)
        assert_raises_rpc_error(
            -4,
            "Pegin amount (0.50) is lower than the minimum pegin amount for this chain (1.00).",
            sidechain.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )
        # check manually constructed
        inputs = [
            {
                "txid": txid,
                "vout": vout,
                "pegin_bitcoin_tx": bitcoin_txhex,
                "pegin_txout_proof": txoutproof,
                "pegin_claim_script": claim_script,
            },
        ]
        fee = Decimal("0.00000363")
        outputs = [
            {sidechain.getnewaddress(): Decimal("0.5") - fee},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        signed = sidechain.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-value-too-low")

        num = PEGIN_SUBSIDY_HEIGHT - sidechain.getblockchaininfo()["blocks"]
        assert num > 0
        self.generate(sidechain, num, sync_fun=sync_sidechain)

        self.log.info(f"===== peg-in subsidy enforcement at height {PEGIN_SUBSIDY_HEIGHT} =====")
        assert_equal(sidechain.getblockchaininfo()["blocks"], PEGIN_SUBSIDY_HEIGHT)

        self.log.info("check new fields for getpeginaddress and getsidechaininfo")
        result = sidechain.getpeginaddress()
        assert_equal(result["pegin_min_amount"], "1.00")
        assert_equal(result["pegin_subsidy_threshold"], "2.00")
        assert_equal(result["pegin_subsidy_height"], PEGIN_SUBSIDY_HEIGHT)
        assert_equal(result["pegin_subsidy_active"], True)
        result = sidechain.getsidechaininfo()
        assert_equal(result["pegin_min_amount"], "1.00")
        assert_equal(result["pegin_subsidy_threshold"], "2.00")
        assert_equal(result["pegin_subsidy_height"], PEGIN_SUBSIDY_HEIGHT)
        assert_equal(result["pegin_subsidy_active"], True)

        # blinded pegins
        self.log.info("blinded pegin below threshold, with validatepegin, subsidy too low")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, subsidy = parent_pegin(parent, sidechain, amount=1.0, feerate=1.0)
        addr = sidechain.getnewaddress(address_type="blech32")
        utxo = sidechain.listunspent()[0]
        changeaddr = sidechain.getrawchangeaddress(address_type="blech32")
        inputs = [
            {
                "txid": txid,
                "vout": vout,
                "pegin_bitcoin_tx": bitcoin_txhex,
                "pegin_txout_proof": txoutproof,
                "pegin_claim_script": claim_script,
            },
            {
                "txid": utxo["txid"],
                "vout": utxo["vout"],
            },
        ]
        fee = Decimal("0.00000363")
        subsidy -= Decimal("0.00000001")
        outputs = [
            {addr: Decimal("1.0") - fee - subsidy},
            {changeaddr: utxo["amount"]},
            {"burn": subsidy},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        blinded = sidechain.blindrawtransaction(hexstring=raw, ignoreblindfail=False)
        signed = sidechain.signrawtransactionwithwallet(blinded)
        assert_equal(signed["complete"], True)
        # node 2 can't validatepegin
        accept = sidechain2.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], True)
        # node 1 will reject
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-subsidy-too-low")
        assert_raises_rpc_error(
            -26,
            "pegin-subsidy-too-low",
            sidechain.sendrawtransaction,
            signed["hex"],
        )

        self.log.info("blinded pegin above threshold, with validatepegin")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=2.0, feerate=1.0)
        addr = sidechain.getnewaddress(address_type="blech32")
        utxo = sidechain.listunspent()[0]
        changeaddr = sidechain.getrawchangeaddress(address_type="blech32")
        inputs = [
            {
                "txid": txid,
                "vout": vout,
                "pegin_bitcoin_tx": bitcoin_txhex,
                "pegin_txout_proof": txoutproof,
                "pegin_claim_script": claim_script,
            },
            {
                "txid": utxo["txid"],
                "vout": utxo["vout"],
            },
        ]
        fee = Decimal("0.00000363")
        outputs = [
            {addr: Decimal("2.0") - fee},
            {changeaddr: utxo["amount"]},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        blinded = sidechain.blindrawtransaction(hexstring=raw, ignoreblindfail=False)
        signed = sidechain.signrawtransactionwithwallet(blinded)
        assert_equal(signed["complete"], True)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], True)
        # =======

        self.log.info("createrawpegin after enforcement, with validatepegin, below threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, parent_fee = parent_pegin(parent, sidechain)
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        assert_equal(pegin_tx["decoded"]["vout"][1]["value"], parent_fee)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("createrawpegin after enforcement, with validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=3.0, feerate=2.0)
        pegintx = sidechain.createrawpegin(bitcoin_txhex, txoutproof, claim_script)
        signed = sidechain.signrawtransactionwithwallet(pegintx["hex"])
        pegin_txid = sidechain.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("createrawpegin after enforcement, without validatepegin, below threshold")
        feerate = 2.0
        txid, vout, txoutproof, bitcoin_txhex, claim_script, parent_fee = parent_pegin(parent, sidechain2, 1.0, feerate)
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied, because validatepegin is off and this pegin requires a burn subsidy.",
            sidechain2.createrawpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        pegintx = sidechain2.createrawpegin(bitcoin_txhex, txoutproof, claim_script, feerate)
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        # without validatepegin the subsidy value is calculated with the claim tx vsize
        assert pegin_tx["decoded"]["vout"][1]["value"] >= parent_fee
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("createrawpegin after enforcement, without validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=2.0, feerate=2.0)
        pegintx = sidechain2.createrawpegin(bitcoin_txhex, txoutproof, claim_script, feerate)
        signed = sidechain2.signrawtransactionwithwallet(pegintx["hex"])
        assert_equal(signed["complete"], True)
        pegin_txid = sidechain2.sendrawtransaction(signed["hex"])
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin after enforcement, with validatepegin, below threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, parent_fee = parent_pegin(parent, sidechain, amount=1.0, feerate=2.0)
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        vsize = pegin_tx["decoded"]["vsize"]
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        assert_equal(pegin_tx["decoded"]["vout"][1]["value"], parent_fee)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin after enforcement, with validatepegin, above threshold")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=2.0, feerate=2.0)
        pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
        pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin after enforcement, without validatepegin, below threshold")
        feerate = 2.0
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, 1.0, feerate)
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied, because validatepegin is off and this pegin requires a burn subsidy.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script, feerate)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        vsize = pegin_tx["decoded"]["vsize"]
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
        assert pegin_tx["decoded"]["vout"][1]["value"] * COIN >= feerate * vsize
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin after enforcement, without validatepegin, above threshold")
        feerate = 2.0
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, 2.0, feerate)
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script, feerate)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 2)
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("claimpegin after enforcement, without validatepegin, below threshold, with incorrect subsidy output")
        # should be accepted by sidechain2 but rejected by the node that is validating pegins
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=1.0, feerate=2.0)
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied, because validatepegin is off and this pegin requires a burn subsidy.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        low_feerate = 1.0
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script, low_feerate)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)

        accept = sidechain.testmempoolaccept([pegin_tx["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-subsidy-too-low")
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=1.99999999, feerate=2.0)
        # validatepegin off means fee rate must be supplied
        assert_raises_rpc_error(
            -8,
            "Bitcoin transaction fee rate must be supplied, because validatepegin is off and this pegin requires a burn subsidy.",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        low_feerate = 1.0
        pegin_txid = sidechain2.claimpegin(bitcoin_txhex, txoutproof, claim_script, low_feerate)
        pegin_tx = sidechain2.gettransaction(pegin_txid, True, True)
        assert_equal(len(pegin_tx["decoded"]["vout"]), 3)

        accept = sidechain.testmempoolaccept([pegin_tx["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-subsidy-too-low")
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        # sub 1sat/vb claim should be rejected
        txid, vout, txoutproof, bitcoin_txhex, claim_script, subsidy = parent_pegin(parent, sidechain, amount=1, feerate=0.1)
        assert_raises_rpc_error(
            -4,
            "Parent transaction must have a feerate of at least 1 sat/vb",
            sidechain.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        # check manually constructed pegin from the sub 1 sat/vb parent
        inputs = [
            {
                "txid": txid,
                "vout": vout,
                "pegin_bitcoin_tx": bitcoin_txhex,
                "pegin_txout_proof": txoutproof,
                "pegin_claim_script": claim_script,
            },
        ]
        fee = Decimal("0.00000363")
        outputs = [
            {addr: Decimal("1.0") - fee - subsidy},
            {"burn": subsidy},
            {"fee": fee},
        ]

        raw = sidechain.createrawtransaction(inputs, outputs)
        signed = sidechain.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-parent-feerate-too-low")

        # =================================

        self.log.info("construct a multi-pegin tx, below threshold")
        feerate = 2.0
        txid1, vout1, txoutproof1, bitcoin_txhex1, claim_script1, parent_fee1 = parent_pegin(parent, sidechain, 0.5, feerate)
        txid2, vout2, txoutproof2, bitcoin_txhex2, claim_script2, parent_fee2 = parent_pegin(parent, sidechain, 1.0, feerate)

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
        subsidy = parent_fee1 + parent_fee2 - Decimal("0.00000001")
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

        subsidy = Decimal("0.00001350")
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
        assert_equal(accept[0]["allowed"], True)
        sidechain.sendrawtransaction(signed["hex"])
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        self.log.info("construct a multi-pegin tx, above threshold")
        txid1, vout1, txoutproof1, bitcoin_txhex1, claim_script1, _ = parent_pegin(parent, sidechain, amount=0.5, feerate=1.0)
        txid2, vout2, txoutproof2, bitcoin_txhex2, claim_script2, _ = parent_pegin(parent, sidechain, amount=1.5, feerate=2.0)
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
            {addr2: Decimal("1.5") - fee},
            {"fee": fee},
        ]
        raw = sidechain.createrawtransaction(inputs, outputs)
        signed = sidechain.signrawtransactionwithwallet(raw)
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], True)
        sidechain.sendrawtransaction(signed["hex"])
        self.generate(sidechain2, 1, sync_fun=sync_sidechain)

        # minimum pegin amount is 1.0
        self.log.info("claimpegin below minimum pegin amount")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount=0.99999999)
        assert_raises_rpc_error(
            -4,
            "Pegin amount (0.99999999) is lower than the minimum pegin amount for this chain (1.00).",
            sidechain2.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )

        # check minimum pegin amount in mempool validation by constructing manually
        self.log.info("rawtransaction below minimum pegin amount")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, subsidy = parent_pegin(parent, sidechain2, amount=0.99999999)
        addr = sidechain2.getnewaddress()
        inputs = [
            {
                "txid": txid,
                "vout": vout,
                "pegin_bitcoin_tx": bitcoin_txhex,
                "pegin_txout_proof": txoutproof,
                "pegin_claim_script": claim_script,
            },
        ]
        fee = Decimal("0.00000363")
        outputs = [
            {addr: Decimal("0.99999999") - fee - subsidy},
            {"burn": subsidy},
            {"fee": fee},
        ]

        raw = sidechain2.createrawtransaction(inputs, outputs)
        signed = sidechain2.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        accept = sidechain2.testmempoolaccept([signed["hex"]])
        # node2 can't check the min pegin amount without validatepegin (could be blinded)
        assert_equal(accept[0]["allowed"], True)
        # node1 rejects below the min pegin amount with validatepegin
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "pegin-value-too-low")
        assert_raises_rpc_error(
            -26,
            "pegin-value-too-low",
            sidechain.sendrawtransaction,
            signed["hex"],
        )
        self.generate(sidechain, 1, sync_fun=sync_sidechain)

        # test various feerates
        self.log.info("claimpegin with validatepegin, below threshold, at various feerates")
        for feerate in [1.0, 1.5, 2.0, 2.3, 3.9, 4.6, 5.2, 6.1, 10.01, 20.7, 22.22, 24.18]:
            txid, vout, txoutproof, bitcoin_txhex, claim_script, parent_fee = parent_pegin(parent, sidechain, 1.0, feerate)
            pegin_txid = sidechain.claimpegin(bitcoin_txhex, txoutproof, claim_script)
            pegin_tx = sidechain.gettransaction(pegin_txid, True, True)
            vsize = pegin_tx["decoded"]["vsize"]
            assert_equal(len(pegin_tx["decoded"]["vout"]), 3)
            assert_equal(pegin_tx["decoded"]["vout"][1]["value"], parent_fee)
            self.generate(sidechain, 1, sync_fun=sync_sidechain)

        # dust error
        # restart node1 with no min pegin amount
        self.stop_node(1, expected_stderr=self.expected_stderr)  # when running with bitcoind as parent node this stderr can occur
        self.start_node(1, extra_args=sidechain.extra_args + ["-peginminamount=0"])
        self.log.info("claimpegin dust error")
        amount = Decimal("0.00000546") if self.options.parent_bitcoin else Decimal("0.00000645")
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain, amount)
        assert_raises_rpc_error(
            -4,
            "Pegin transaction would create dust output. See the log for details.",
            sidechain.claimpegin,
            bitcoin_txhex,
            txoutproof,
            claim_script,
        )
        # check dust mempool validation by constructing manually
        txid, vout, txoutproof, bitcoin_txhex, claim_script, _ = parent_pegin(parent, sidechain2, amount=0.00001570)
        addr = sidechain2.getnewaddress()
        inputs = [
            {
                "txid": txid,
                "vout": vout,
                "pegin_bitcoin_tx": bitcoin_txhex,
                "pegin_txout_proof": txoutproof,
                "pegin_claim_script": claim_script,
            },
        ]
        fee = Decimal("0.00000363")
        subsidy = Decimal("0.00001194")
        outputs = [
            {addr: Decimal("0.00001570") - fee - subsidy},  # 14 sats is dust at 0.1 sat/vb dustrelayfee
            {"burn": subsidy},
            {"fee": fee},
        ]

        raw = sidechain2.createrawtransaction(inputs, outputs)
        signed = sidechain2.signrawtransactionwithwallet(raw)
        assert_equal(signed["complete"], True)
        accept = sidechain2.testmempoolaccept([signed["hex"]])
        # mempool validation already checks for dust outputs
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "dust")
        accept = sidechain.testmempoolaccept([signed["hex"]])
        assert_equal(accept[0]["allowed"], False)
        assert_equal(accept[0]["reject-reason"], "dust")
        assert_raises_rpc_error(
            -26,
            "dust",
            sidechain.sendrawtransaction,
            signed["hex"],
        )

        # Manually stop sidechains first, then the parent chain.
        self.stop_node(2)
        self.stop_node(1, expected_stderr=self.expected_stderr)  # when running with bitcoind as parent node this stderr can occur
        self.stop_node(0)


if __name__ == "__main__":
    PeginSubsidyTest().main()
