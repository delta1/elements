// Copyright (c) 2017-2017 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <pegins.h>

#include <addresstype.h>
#include <arith_uint256.h>
#include <block_proof.h>
#include <chainparams.h>
#include <crypto/hmac_sha256.h>
#include <consensus/consensus.h>
#include <consensus/validation.h>
#include <mainchainrpc.h>
#include <merkleblock.h>
#include <pow.h>
#include <primitives/transaction.h>
#include <primitives/bitcoin/merkleblock.h>
#include <secp256k1.h>
#include <script/interpreter.h>
#include <streams.h>
#include <dynafed.h>

//
// ELEMENTS
//

#include <validation.h>

// calculate_contract() and MatchLiquidWatchman() moved to src/script/pegins.cpp
// (bitcoin_common) so the descriptor engine can call them. They are declared in
// <script/pegins.h>, which is pulled in transitively via <pegins.h>.

bool GetAmountFromParentChainPegin(CAmount& amount, const Sidechain::Bitcoin::CTransaction& txBTC, unsigned int nOut)
{
    amount = txBTC.vout[nOut].nValue;
    return true;
}

bool GetAmountFromParentChainPegin(CAmount& amount, const CTransaction& txBTC, unsigned int nOut)
{
    if (!txBTC.vout[nOut].nValue.IsExplicit()) {
        return false;
    }
    if (!txBTC.vout[nOut].nAsset.IsExplicit()) {
        return false;
    }
    if (txBTC.vout[nOut].nAsset.GetAsset() != Params().GetConsensus().parent_pegged_asset) {
        return false;
    }
    amount = txBTC.vout[nOut].nValue.GetAmount();
    return true;
}

template<typename T>
static bool CheckPeginTx(const std::vector<unsigned char>& tx_data, T& pegtx, const COutPoint& prevout, const CAmount claim_amount, const CScript& claim_script, const std::vector<std::pair<CScript, CScript>>& fedpegscripts)
{
    try {
        DataStream pegtx_stream(tx_data);
        pegtx_stream >> TX_WITH_WITNESS(pegtx);
        if (!pegtx_stream.empty()) {
            return false;
        }
    } catch (std::exception&) {
        // Invalid encoding of transaction
        return false;
    }

    // Check that transaction matches txid
    if (pegtx->GetHash() != prevout.hash) {
        return false;
    }

    if (prevout.n >= pegtx->vout.size()) {
        return false;
    }
    CAmount amount = 0;
    if (!GetAmountFromParentChainPegin(amount, *pegtx, prevout.n)) {
        return false;
    }
    // Check the transaction nout/value matches
    if (claim_amount != amount) {
        return false;
    }

    // Check that the witness program matches the p2ch on the (p2sh-)p2wsh
    // transaction output. We support multiple scripts as a grace period for peg-in users
    for (const auto& scripts : fedpegscripts) {
        int fedpeg_version = 0;
        std::vector<unsigned char> fedpeg_program;
        scripts.first.IsWitnessProgram(fedpeg_version, fedpeg_program);
        // We immediately return true if any fedpegscripts are unencumbered
        // by currently-known parent chain segwit versions.
        // TODO: Refactor for future versionbits deployment of parent-segwit version
        if (fedpeg_version > 0) {
            return true;
        }
        CScript tweaked_fedpegscript = calculate_contract(scripts.second, claim_script);
        CScript expected_script(GetScriptForDestination(WitnessV0ScriptHash(tweaked_fedpegscript)));
        if (scripts.first.IsPayToScriptHash()) {
            expected_script = GetScriptForDestination(ScriptHash(expected_script));
        }
        if (pegtx->vout[prevout.n].scriptPubKey == expected_script) {
            return true;
        }
    }
    return false;
}

template<typename T>
static bool GetBlockAndTxFromMerkleBlock(uint256& block_hash, uint256& tx_hash, unsigned int& tx_index, T& merkle_block, const std::vector<unsigned char>& merkle_block_raw)
{
    try {
        std::vector<uint256> tx_hashes;
        std::vector<unsigned int> tx_indices;
        DataStream merkle_block_stream{merkle_block_raw};
        merkle_block_stream >> TX_NO_WITNESS(merkle_block);
        block_hash = merkle_block.header.GetHash();

        if (!merkle_block_stream.empty()) {
           return false;
        }
        if (merkle_block.txn.ExtractMatches(tx_hashes, tx_indices) != merkle_block.header.hashMerkleRoot || tx_hashes.size() != 1) {
            return false;
        }
        tx_hash = tx_hashes[0];
        tx_index = tx_indices[0];
    } catch (std::exception&) {
        // Invalid encoding of merkle block
        return false;
    }
    return true;
}

bool CheckParentProofOfWork(uint256 hash, unsigned int nBits, const Consensus::Params& params)
{
    bool fNegative;
    bool fOverflow;
    arith_uint256 bnTarget;

    bnTarget.SetCompact(nBits, &fNegative, &fOverflow);

    // Check range
    if (fNegative || bnTarget == 0 || fOverflow || bnTarget > UintToArith256(params.parentChainPowLimit))
        return false;

    // Check proof of work matches claimed amount
    if (UintToArith256(hash) > bnTarget)
        return false;

    return true;
}

bool IsValidPeginWitness(const CScriptWitness& pegin_witness, const std::vector<std::pair<CScript, CScript>>& fedpegscripts, const COutPoint& prevout, std::string& err_msg, bool check_depth, bool* depth_failed) {
    if (depth_failed) {
        *depth_failed = false;
    }

    // 0) Return false if !consensus.has_parent_chain
    if (!Params().GetConsensus().has_parent_chain) {
        err_msg = "Parent chain is not enabled on this network.";
        return false;
    }

    // Format on stack is as follows:
    // 1) value - the value of the pegin output
    // 2) asset type - the asset type being pegged in
    // 3) genesis blockhash - genesis block of the parent chain
    // 4) claim script - script to be evaluated for spend authorization
    // 5) serialized transaction - serialized bitcoin transaction
    // 6) txout proof - merkle proof connecting transaction to header
    //
    // First 4 values(plus prevout) are enough to validate a peg-in without any internal knowledge
    // of Bitcoin serialization. This is useful for further abstraction by outsourcing
    // the other validity checks to RPC calls.

    const std::vector<std::vector<unsigned char> >& stack = pegin_witness.stack;
    // Must include all elements
    if (stack.size() != 6) {
        err_msg = "Not enough stack items.";
        return false;
    }

    DataStream stream{stack[0]};
    CAmount value;
    try {
        stream >> value;
    } catch (...) {
        err_msg = "Could not deserialize value.";
        return false;
    }

    if (!MoneyRange(value)) {
        err_msg = "Value was not in valid value range.";
        return false;
    }

    // Get asset type
    if (stack[1].size() != 32) {
        err_msg = "Asset type was not 32 bytes.";
        return false;
    }
    CAsset asset(stack[1]);

    // Get genesis blockhash
    if (stack[2].size() != 32) {
        err_msg = "Parent genesis blockchaash was not 32 bytes.";
        return false;
    }
    uint256 gen_hash(stack[2]);

    // Get claim_script, sanity check size
    CScript claim_script(stack[3].begin(), stack[3].end());
    if (claim_script.size() > 100) {
        err_msg = "Claim script is too large.";
        return false;
    }

    uint256 block_hash;
    uint256 tx_hash;
    int num_txs;
    unsigned int tx_index = 0;
    // Get txout proof
    if (Params().GetConsensus().ParentChainHasPow()) {
        Sidechain::Bitcoin::CMerkleBlock merkle_block_pow;
        if (!GetBlockAndTxFromMerkleBlock(block_hash, tx_hash, tx_index, merkle_block_pow, stack[5])) {
            err_msg = "Could not extract block and tx from merkleblock.";
            return false;
        }
        if (!CheckParentProofOfWork(block_hash, merkle_block_pow.header.nBits, Params().GetConsensus())) {
            err_msg = "Parent proof of work is invalid or insufficient.";
            return false;
        }

        Sidechain::Bitcoin::CTransactionRef pegtx;
        if (!CheckPeginTx(stack[4], pegtx, prevout, value, claim_script, fedpegscripts)) {
            err_msg = "Peg-in tx is invalid.";
            return false;
        }

        num_txs = merkle_block_pow.txn.GetNumTransactions();
    } else {
        CMerkleBlock merkle_block;
        if (!GetBlockAndTxFromMerkleBlock(block_hash, tx_hash, tx_index, merkle_block, stack[5])) {
            err_msg = "Could not extract block and tx from merkleblock.";
            return false;
        }

        if (!CheckProofSignedParent(merkle_block.header, Params().GetConsensus())) {
            err_msg = "Parent signed block is invalid.";
            return false;
        }

        CTransactionRef pegtx;
        if (!CheckPeginTx(stack[4], pegtx, prevout, value, claim_script, fedpegscripts)) {
            err_msg = "Peg-in tx is invalid.";
            return false;
        }

        num_txs = merkle_block.txn.GetNumTransactions();
    }

    // Check that the merkle proof corresponds to the txid
    if (prevout.hash != tx_hash) {
        err_msg = "Merkle proof and txid mismatch.";
        return false;
    }

    // Check the genesis block corresponds to a valid peg (only one for now)
    if (gen_hash != Params().ParentGenesisBlockHash()) {
        err_msg = "Parent genesis block mismatch.";
        return false;
    }

    // Check the asset type corresponds to a valid pegged asset (only one for now)
    if (asset != Params().GetConsensus().pegged_asset) {
        return false;
    }

    // Finally, validate peg-in via rpc call
    if (check_depth && gArgs.GetBoolArg("-validatepegin", Params().GetConsensus().has_parent_chain)) {
        unsigned int required_depth = Params().GetConsensus().pegin_min_depth;
        // Don't allow coinbase output claims before coinbase maturity
        if (tx_index == 0) {
            required_depth = std::max(required_depth, (unsigned int)COINBASE_MATURITY);
        }
        if (!IsConfirmedBitcoinBlock(block_hash, required_depth, num_txs)) {
            err_msg = "Needs more confirmations.";
            if (depth_failed) {
                *depth_failed = true;
            }
            return false;
        }
    }
    return true;
}

std::vector<std::pair<CScript, CScript>> GetValidFedpegScripts(const CBlockIndex* pblockindex, const Consensus::Params& params, bool nextblock_validation)
{
    assert(pblockindex);

    std::vector<std::pair<CScript, CScript>> fedpegscripts;

    const int32_t epoch_length = (int32_t) params.dynamic_epoch_length;
    const int32_t epoch_age = pblockindex->nHeight % epoch_length;
    const int32_t epoch_start_height = pblockindex->nHeight - epoch_age;

    // In mempool and general "enforced next block" RPC we need to look ahead one block
    // to see if we're on a boundary. If so, put that epoch's fedpegscript in place
    if (nextblock_validation && epoch_age == epoch_length - 1) {
        DynaFedParamEntry next_param = ComputeNextBlockFullCurrentParameters(pblockindex, params);
        fedpegscripts.emplace_back(next_param.m_fedpeg_program, next_param.m_fedpegscript);
    }

    // Next we walk backwards up to M epoch starts
    for (int32_t i = 0; i < (int32_t) params.total_valid_epochs; i++) {
        // We are within total_valid_epochs of the genesis
        if (i * epoch_length > epoch_start_height) {
            break;
        }

        const CBlockIndex* p_epoch_start = pblockindex->GetAncestor(epoch_start_height-i*epoch_length);

        // We're done here, for whatever reason.
        if (!p_epoch_start) {
            break;
        }

        if (node::fTrimHeaders) {
            LOCK(cs_main);
            ForceUntrimHeader(p_epoch_start);
        }
        if (!p_epoch_start->dynafed_params().IsNull()) {
            fedpegscripts.emplace_back(p_epoch_start->dynafed_params().m_current.m_fedpeg_program, p_epoch_start->dynafed_params().m_current.m_fedpegscript);
        } else {
            fedpegscripts.emplace_back(GetScriptForDestination(ScriptHash(GetScriptForDestination(WitnessV0ScriptHash(params.fedpegScript)))), params.fedpegScript);
        }
    }
    // Only return up to the latest total_valid_epochs fedpegscripts, which are enforced
    fedpegscripts.resize(std::min(fedpegscripts.size(), params.total_valid_epochs));
    return fedpegscripts;
}

template<typename T_tx_ref, typename T_merkle_block>
CScriptWitness CreatePeginWitnessInner(const CAmount& value, const CAsset& asset, const uint256& genesis_hash, const CScript& claim_script, const T_tx_ref& tx_ref, const T_merkle_block& merkle_block)
{
    std::vector<unsigned char> value_bytes;
    VectorWriter ss_val(value_bytes, 0);
    try {
        ss_val << value;
    } catch (...) {
        throw std::ios_base::failure("Amount serialization is invalid.");
    }

    // Strip witness data for proof inclusion since only TXID-covered fields matters
    DataStream ss_tx{};
    ss_tx << TX_NO_WITNESS(tx_ref);
    const auto* ss_tx_ptr = UCharCast(ss_tx.data());
    std::vector<unsigned char> tx_data_stripped(ss_tx_ptr, ss_tx_ptr + ss_tx.size());

    // Serialize merkle block
    DataStream ss_txout_proof{};
    ss_txout_proof << TX_NO_WITNESS(merkle_block);
    const auto* ss_txout_ptr = UCharCast(ss_txout_proof.data());
    std::vector<unsigned char> txout_proof_bytes(ss_txout_ptr, ss_txout_ptr + ss_txout_proof.size());

    // Construct pegin proof
    CScriptWitness pegin_witness;
    std::vector<std::vector<unsigned char>>& stack = pegin_witness.stack;
    stack.push_back(value_bytes);
    stack.emplace_back(asset.begin(), asset.end());
    stack.emplace_back(genesis_hash.begin(), genesis_hash.end());
    stack.emplace_back(claim_script.begin(), claim_script.end());
    stack.push_back(tx_data_stripped);
    stack.push_back(txout_proof_bytes);
    return pegin_witness;
}

CScriptWitness CreatePeginWitness(const CAmount& value, const CAsset& asset, const uint256& genesis_hash, const CScript& claim_script, const CTransactionRef& tx_ref, const CMerkleBlock& merkle_block)
{
    return CreatePeginWitnessInner(value, asset, genesis_hash, claim_script, tx_ref, merkle_block);
}
CScriptWitness CreatePeginWitness(const CAmount& value, const CAsset& asset, const uint256& genesis_hash, const CScript& claim_script, const Sidechain::Bitcoin::CTransactionRef& tx_ref, const Sidechain::Bitcoin::CMerkleBlock& merkle_block)
{
    return CreatePeginWitnessInner(value, asset, genesis_hash, claim_script, tx_ref, merkle_block);
}

bool DecomposePeginWitness(const CScriptWitness& witness, CAmount& value, CAsset& asset, uint256& genesis_hash, CScript& claim_script, std::variant<std::monostate, Sidechain::Bitcoin::CTransactionRef, CTransactionRef>& tx, std::variant<std::monostate, Sidechain::Bitcoin::CMerkleBlock, CMerkleBlock>& merkle_block)
{
    const auto& stack = witness.stack;

    if (stack.size() != 6) return false;

    DataStream stream{stack[0]};
    stream >> value;

    CAsset tmp_asset(stack[1]);
    asset = tmp_asset;

    uint256 gh(stack[2]);
    genesis_hash = gh;

    CScript s(stack[3].begin(), stack[3].end());
    claim_script = s;

    DataStream ss_tx(stack[4]);
    if (Params().GetConsensus().ParentChainHasPow()) {
        Sidechain::Bitcoin::CTransactionRef btc_tx;
        ss_tx >> TX_WITH_WITNESS(btc_tx);
        tx = btc_tx;
    } else {
        CTransactionRef elem_tx;
        ss_tx >> TX_WITH_WITNESS(elem_tx);
        tx = elem_tx;
    }

    DataStream ss_proof(stack[5]);
    if (Params().GetConsensus().ParentChainHasPow()) {
        Sidechain::Bitcoin::CMerkleBlock tx_proof;
        ss_proof >> TX_WITH_WITNESS(tx_proof);
        merkle_block = tx_proof;
    } else {
        CMerkleBlock tx_proof;
        ss_proof >> TX_WITH_WITNESS(tx_proof);
        merkle_block = tx_proof;
    }

    return true;
}
