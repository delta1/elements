// Copyright (c) 2020-2020 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

// ELEMENTS

#include <primitives/transaction.h>
#include <script/pegins.h>
#include <script/script.h>
#include <streams.h>

#include <crypto/hmac_sha256.h>
#include <secp256k1.h>

#include <cassert>
#include <cstring>

namespace {
static secp256k1_context* secp256k1_ctx_pegin;

class Secp256k1PeginCtx
{
public:
    Secp256k1PeginCtx() {
        assert(secp256k1_ctx_pegin == nullptr);
        secp256k1_ctx_pegin = secp256k1_context_create(SECP256K1_CONTEXT_VERIFY | SECP256K1_CONTEXT_SIGN);
        assert(secp256k1_ctx_pegin != nullptr);
    }

    ~Secp256k1PeginCtx() {
        assert(secp256k1_ctx_pegin != nullptr);
        secp256k1_context_destroy(secp256k1_ctx_pegin);
        secp256k1_ctx_pegin = nullptr;
    }
};
static Secp256k1PeginCtx instance_of_secp256k1_pegin_ctx;
} // namespace

// Takes federation redeem script and adds HMAC_SHA256(pubkey, scriptPubKey) as a tweak to each pubkey
CScript calculate_contract(const CScript& federation_script, const CScript& scriptPubKey) {
    CScript scriptDestination;

    bool is_liquidv1_watchman = MatchLiquidWatchman(federation_script);

    CScript::const_iterator sdpc = federation_script.begin();
    std::vector<unsigned char> vch;
    opcodetype opcodeTmp;
    bool liquid_op_else_found = false;
    while (federation_script.GetOp(sdpc, opcodeTmp, vch))
    {

        // For liquidv1 initial watchman template, don't tweak emergency keys
        if (is_liquidv1_watchman && opcodeTmp == OP_ELSE) {
            liquid_op_else_found = true;
        }

        size_t pub_len = 33;
        if (vch.size() == pub_len && !liquid_op_else_found)
        {
            unsigned char tweak[32];
            CHMAC_SHA256(vch.data(), pub_len).Write(scriptPubKey.data(), scriptPubKey.size()).Finalize(tweak);
            int ret;
            secp256k1_pubkey watchman;
            secp256k1_pubkey tweaked;
            ret = secp256k1_ec_pubkey_parse(secp256k1_ctx_pegin, &watchman, vch.data(), pub_len);
            assert(ret == 1);
            ret = secp256k1_ec_pubkey_parse(secp256k1_ctx_pegin, &tweaked, vch.data(), pub_len);
            assert(ret == 1);
            // If someone creates a tweak that makes this fail, they broke SHA256
            ret = secp256k1_ec_pubkey_tweak_add(secp256k1_ctx_pegin, &tweaked, tweak);
            assert(ret == 1);
            unsigned char new_pub[33];
            ret = secp256k1_ec_pubkey_serialize(secp256k1_ctx_pegin, new_pub, &pub_len, &tweaked, SECP256K1_EC_COMPRESSED);
            assert(ret == 1);
            assert(pub_len == 33);

            // push tweaked pubkey
            std::vector<unsigned char> pub_vec(new_pub, new_pub + pub_len);
            scriptDestination << pub_vec;

            // Sanity checks to reduce pegin risk. If the tweaked
            // value flips a bit, we may lose pegin funds irretrievably.
            // We take the tweak, derive its pubkey and check that
            // `tweaked - watchman = tweak` to check the computation
            // two different ways
            secp256k1_pubkey tweaked2;
            ret = secp256k1_ec_pubkey_create(secp256k1_ctx_pegin, &tweaked2, tweak);
            assert(ret);
            ret = secp256k1_ec_pubkey_negate(secp256k1_ctx_pegin, &watchman);
            assert(ret);
            secp256k1_pubkey* pubkey_combined[2];
            pubkey_combined[0] = &watchman;
            pubkey_combined[1] = &tweaked;
            secp256k1_pubkey maybe_tweaked2;
            ret = secp256k1_ec_pubkey_combine(secp256k1_ctx_pegin, &maybe_tweaked2, pubkey_combined, 2);
            assert(ret);
            assert(!memcmp(&maybe_tweaked2, &tweaked2, 64));
        } else {
            // add to script untouched
            if (vch.size() > 0) {
                scriptDestination << vch;
            } else {
                scriptDestination << opcodeTmp;
            }
        }
    }

    return scriptDestination;
}

bool MatchLiquidWatchman(const CScript& script)
{
    CScript::const_iterator it = script.begin();
    std::vector<unsigned char> data;
    opcodetype opcode;

    // Stack depth check for branch choice
    if (!script.GetOp(it, opcode, data) || opcode != OP_DEPTH) {
        return false;
    }
    // Take in value, then check equality
    if (!script.GetOp(it, opcode, data) ||
            !script.GetOp(it, opcode, data) ||
            opcode != OP_EQUAL) {
        return false;
    }
    // IF EQUAL
    if (!script.GetOp(it, opcode, data) || opcode != OP_IF) {
        return false;
    }
    // Take in value k, make sure minimally encoded number from 1 to 16
    if (!script.GetOp(it, opcode, data) ||
            opcode > OP_16 ||
            (opcode < OP_1NEGATE && !CheckMinimalPush(data, opcode))) {
        return false;
    }
    opcodetype opcode2 = opcode;
    std::vector<unsigned char> num = data;
    // Iterate through multisig stuff until ELSE is hit
    while (opcode != OP_ELSE) {
        if (!script.GetOp(it, opcode, data)) {
            return false;
        }
    }
    // Take minimally-encoded CSV push number k'
    if (!script.GetOp(it, opcode, data) ||
            opcode > OP_16 || (opcode < OP_1NEGATE && !CheckMinimalPush(data, opcode))) {
        return false;
    }
    // CSV
    if (!script.GetOp(it, opcode, data) || opcode != OP_CHECKSEQUENCEVERIFY) {
        return false;
    }
    // Drop the CSV number
    if (!script.GetOp(it, opcode, data) || opcode != OP_DROP) {
        return false;
    }
    // Take the minimally-encoded n of k-of-n multisig arg
    if (!script.GetOp(it, opcode, data) ||
            opcode > OP_16 || (opcode < OP_1NEGATE && !CheckMinimalPush(data, opcode)) ) {
        return false;
    }

    // The two multisig k-numbers must not match, otherwise ELSE branch can not be reached
    if (opcode == opcode2 && num == data) {
        return false;
    }

    // Find the ENDIF
    while (opcode != OP_ENDIF) {
        if (!script.GetOp(it, opcode, data)) {
            return false;
        }
    }
    // CHECKMULTISIG
    if (!script.GetOp(it, opcode, data) || opcode != OP_CHECKMULTISIG) {
        return false;
    }
    // No more pushes
    return (it == script.end());
}

CTxOut GetPeginOutputFromWitness(const CScriptWitness& pegin_witness) {
    if (pegin_witness.stack.size() < 4) {
        return CTxOut();
    }

    DataStream stream{pegin_witness.stack[0]};
    CAmount value;
    stream >> value;

    return CTxOut(CAsset(pegin_witness.stack[1]), CConfidentialValue(value), CScript(pegin_witness.stack[3].begin(), pegin_witness.stack[3].end()));
}
