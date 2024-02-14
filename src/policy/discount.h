// Copyright (c) 2009-2010 Satoshi Nakamoto
// Copyright (c) 2009-2021 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#ifndef BITCOIN_POLICY_DISCOUNT_H
#define BITCOIN_POLICY_DISCOUNT_H

#include <consensus/consensus.h>
#include <cstdint>
#include <primitives/transaction.h>
#include <version.h>

/**
 * Calculate a smaller virtual size for discounted Confidential Transactions.
 */
static inline int64_t GetDiscountedVirtualTransactionSize(const CTransaction& tx)
{
    size_t weight = ::GetSerializeSize(tx, PROTOCOL_VERSION | SERIALIZE_TRANSACTION_NO_WITNESS) * (WITNESS_SCALE_FACTOR - 1) + ::GetSerializeSize(tx, PROTOCOL_VERSION);

    // for each confidential output, subtract the output witness weight
    for (size_t i = 0; i < tx.vout.size(); ++i) {
        const CTxOut& output = tx.vout[i];
        if (output.IsFee()) continue;
        if (output.nAsset.IsCommitment() && output.nValue.IsCommitment()) {
            weight -= ::GetSerializeSize(tx.witness.vtxoutwit[i], PROTOCOL_VERSION);
        }
    }
    assert(weight > 0);

    size_t discountvsize = (weight + WITNESS_SCALE_FACTOR - 1) / WITNESS_SCALE_FACTOR;

    assert(discountvsize > 0);
    return discountvsize;
}

#endif // BITCOIN_POLICY_DISCOUNT_H
