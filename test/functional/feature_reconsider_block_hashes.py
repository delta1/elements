#!/usr/bin/env python3
# Copyright (c) 2026 The Elements Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test automatically reconsidering historical hard-fork blocks on startup.

Simulates the liquidv1 scenario where blocks containing hard-forking
transactions were invalidated by unupgraded nodes.
On restart with the blocks' hashes configured via
consensus.hashesReconsiderBlock (exposed for testing as
-con_reconsiderblockhashes, which may be passed multiple times), the node
should automatically clear each block's failed status and reconnect the
best-work chain, without any manual reconsiderblock call.

Two independently-invalidated forks are used (rather than two hard-fork
blocks on the same chain) because invalidating a block also invalidates all
of its descendants: reconsidering the earliest invalid block on a single
chain would already restore every block after it, so that wouldn't actually
exercise more than one entry in the hash list.
"""

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal


class ReconsiderHardforkBlockTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 1

    def run_test(self):
        node = self.nodes[0]

        self.log.info("Mine a common ancestor chain")
        self.generate(node, 4, sync_fun=self.no_op)
        fork_point = node.getbestblockhash()

        self.log.info("Mine fork A; its block at height 5 stands in for a hard-fork block")
        hardfork_hash_a = self.generate(node, 1, sync_fun=self.no_op)[0]
        tip_a = self.generate(node, 2, sync_fun=self.no_op)[-1]
        assert_equal(node.getblockcount(), 7)

        self.log.info("Simulate unupgraded nodes rejecting fork A's hard-fork block")
        node.invalidateblock(hardfork_hash_a)
        assert_equal(node.getbestblockhash(), fork_point)
        assert_equal(node.getblockcount(), 4)

        self.log.info("Mine fork B from the same ancestor; its block at height 5 stands in for a second, independent hard-fork block")
        # Mine to a different address than fork A used, so this block (same height, same
        # parent, same simulated time) isn't byte-for-byte identical to fork A's block 5.
        fork_b_address = node.PRIV_KEYS[1].address
        hardfork_hash_b = self.generatetoaddress(node, 1, fork_b_address, sync_fun=self.no_op)[0]
        self.generate(node, 1, sync_fun=self.no_op)
        assert_equal(node.getblockcount(), 6)

        self.log.info("Simulate old software rejecting fork B's hard-fork block too")
        node.invalidateblock(hardfork_hash_b)
        assert_equal(node.getbestblockhash(), fork_point)
        assert_equal(node.getblockcount(), 4)
        # invalidateblock should also roll back the best-header tracker, not just the tip
        assert_equal(node.getblockchaininfo()["headers"], node.getblockchaininfo()["blocks"])

        self.log.info("Restarting without the exception configured leaves both forks invalidated")
        self.restart_node(0)
        assert_equal(node.getblockcount(), 4)
        assert_equal(node.getblockchaininfo()["headers"], node.getblockchaininfo()["blocks"])

        self.log.info("Restarting with -con_reconsiderblockhashes for both hashes reconnects the best-work fork automatically")
        with node.assert_debug_log(expected_msgs=[
                f"Reconsidering block {hardfork_hash_a}",
                f"Reconsidering block {hardfork_hash_b}",
        ]):
            self.restart_node(0, extra_args=[
                f"-con_reconsiderblockhashes={hardfork_hash_a}",
                f"-con_reconsiderblockhashes={hardfork_hash_b}",
            ])
        # Fork A has more work (3 blocks vs. fork B's 2), so it becomes the active chain
        # even though fork B's hash was reconsidered too.
        assert_equal(node.getblockcount(), 7)
        assert_equal(node.getbestblockhash(), tip_a)
        # headers must catch up too (ChainstateManager::m_best_header, separate from the
        # connected-blocks tip) -- regression check for the RecalculateBestHeader() call
        assert_equal(node.getblockchaininfo()["headers"], node.getblockchaininfo()["blocks"])

        self.log.info("The node continues operating normally on the reconnected chain")
        self.generate(node, 1, sync_fun=self.no_op)
        assert_equal(node.getblockcount(), 8)


if __name__ == '__main__':
    ReconsiderHardforkBlockTest(__file__).main()
