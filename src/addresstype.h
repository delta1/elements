// Copyright (c) 2023 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or https://www.opensource.org/licenses/mit-license.php.

#ifndef BITCOIN_ADDRESSTYPE_H
#define BITCOIN_ADDRESSTYPE_H

#include <pubkey.h>
#include <script/script.h>
#include <uint256.h>
#include <util/hash_type.h>

#include <variant>
#include <algorithm>

class CNoDestination {
public:
    friend bool operator==(const CNoDestination &a, const CNoDestination &b) { return true; }
    friend bool operator<(const CNoDestination &a, const CNoDestination &b) { return true; }
};

struct PKHash : public BaseHash<uint160>
{
    PKHash() : BaseHash() {}
    explicit PKHash(const uint160& hash) : BaseHash(hash) {}
    explicit PKHash(const CPubKey& pubkey);
    explicit PKHash(const CKeyID& pubkey_id);
    // ELEMENTS
    explicit PKHash(const CPubKey& pubkey, const CPubKey& blinding_pubkey);
    explicit PKHash(const CKeyID& hash, const CPubKey& blinding_pubkey);
    CPubKey blinding_pubkey;
};
CKeyID ToKeyID(const PKHash& key_hash);

struct WitnessV0KeyHash;

struct ScriptHash : public BaseHash<uint160>
{
    ScriptHash() : BaseHash() {}
    // These don't do what you'd expect.
    // Use ScriptHash(GetScriptForDestination(...)) instead.
    explicit ScriptHash(const WitnessV0KeyHash& hash) = delete;
    explicit ScriptHash(const PKHash& hash) = delete;

    explicit ScriptHash(const uint160& hash) : BaseHash(hash) {}
    explicit ScriptHash(const CScript& script);
    explicit ScriptHash(const CScriptID& script);
    // ELEMENTS
    explicit ScriptHash(const CScript& script, const CPubKey& blinding_pubkey);
    explicit ScriptHash(const CScriptID& hash, const CPubKey& blinding_pubkey);
    CPubKey blinding_pubkey;
};
CScriptID ToScriptID(const ScriptHash& script_hash);

struct WitnessV0ScriptHash : public BaseHash<uint256>
{
    WitnessV0ScriptHash() : BaseHash() {}
    explicit WitnessV0ScriptHash(const uint256& hash) : BaseHash(hash) {}
    explicit WitnessV0ScriptHash(const CScript& script);
    // ELEMENTS
    explicit WitnessV0ScriptHash(const CScript& script, const CPubKey& blinding_pubkey);
    CPubKey blinding_pubkey;
};

struct WitnessV0KeyHash : public BaseHash<uint160>
{
    WitnessV0KeyHash() : BaseHash() {}
    explicit WitnessV0KeyHash(const uint160& hash) : BaseHash(hash) {}
    explicit WitnessV0KeyHash(const CPubKey& pubkey);
    explicit WitnessV0KeyHash(const PKHash& pubkey_hash);
    // ELEMENTS
    explicit WitnessV0KeyHash(const PKHash& hash, const CPubKey& blinding_pubkey_in) : BaseHash(hash), blinding_pubkey(blinding_pubkey_in) {}
    CPubKey blinding_pubkey;
};
CKeyID ToKeyID(const WitnessV0KeyHash& key_hash);

struct WitnessV1Taproot : public XOnlyPubKey
{
    WitnessV1Taproot() : XOnlyPubKey() {}
    explicit WitnessV1Taproot(const XOnlyPubKey& xpk) : XOnlyPubKey(xpk) {}
    // ELEMENTS
    CPubKey blinding_pubkey;
};

//! CTxDestination subtype to encode any future Witness version
struct WitnessUnknown
{
    unsigned int version;
    unsigned int length;
    unsigned char program[40];
    CPubKey blinding_pubkey; // ELEMENTS

    friend bool operator==(const WitnessUnknown& w1, const WitnessUnknown& w2) {
        if (w1.version != w2.version) return false;
        if (w1.length != w2.length) return false;
        return std::equal(w1.program, w1.program + w1.length, w2.program);
    }

    friend bool operator<(const WitnessUnknown& w1, const WitnessUnknown& w2) {
        if (w1.version < w2.version) return true;
        if (w1.version > w2.version) return false;
        if (w1.length < w2.length) return true;
        if (w1.length > w2.length) return false;
        return std::lexicographical_compare(w1.program, w1.program + w1.length, w2.program, w2.program + w2.length);
    }
};

// ELEMENTS:
class NullData
{
public:
    std::vector<std::vector<unsigned char>> null_data;
    friend bool operator==(const NullData &a, const NullData &b) { return  true; }
    friend bool operator<(const NullData &a, const NullData &b) { return  true; }

    NullData& operator<<(std::vector<unsigned char> b)
    {
        null_data.push_back(b);
        return *this;
    }
};

/**
 * A txout script template with a specific destination. It is either:
 *  * CNoDestination: no destination set
 *  * PKHash: TxoutType::PUBKEYHASH destination (P2PKH)
 *  * ScriptHash: TxoutType::SCRIPTHASH destination (P2SH)
 *  * WitnessV0ScriptHash: TxoutType::WITNESS_V0_SCRIPTHASH destination (P2WSH)
 *  * WitnessV0KeyHash: TxoutType::WITNESS_V0_KEYHASH destination (P2WPKH)
 *  * WitnessV1Taproot: TxoutType::WITNESS_V1_TAPROOT destination (P2TR)
 *  * WitnessUnknown: TxoutType::WITNESS_UNKNOWN destination (P2W???)
 *  * NullData: TxoutType::NULL_DATA destination (OP_RETURN) // ELEMENTS
 *  A CTxDestination is the internal data type encoded in a bitcoin address
 */
using CTxDestination = std::variant<CNoDestination, PKHash, ScriptHash, WitnessV0ScriptHash, WitnessV0KeyHash, WitnessV1Taproot, WitnessUnknown, NullData>;

/** Check whether a CTxDestination is a CNoDestination. */
bool IsValidDestination(const CTxDestination& dest);

/**
 * Parse a standard scriptPubKey for the destination address. Assigns result to
 * the addressRet parameter and returns true if successful. Currently only works for P2PK,
 * P2PKH, P2SH, P2WPKH, and P2WSH scripts.
 */
bool ExtractDestination(const CScript& scriptPubKey, CTxDestination& addressRet);

/**
 * Generate a Bitcoin scriptPubKey for the given CTxDestination. Returns a P2PKH
 * script for a CKeyID destination, a P2SH script for a CScriptID, and an empty
 * script for CNoDestination.
 */
CScript GetScriptForDestination(const CTxDestination& dest);

#endif // BITCOIN_ADDRESSTYPE_H
