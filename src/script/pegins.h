// Copyright (c) 2020-2020 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#ifndef BITCOIN_SCRIPT_PEGINS_H
#define BITCOIN_SCRIPT_PEGINS_H

#include <primitives/transaction.h>
#include <script/script.h>

// Constructs unblinded output to be used in amount and scriptpubkey checks during pegin
CTxOut GetPeginOutputFromWitness(const CScriptWitness& pegin_witness);

// Takes federation redeem script and adds HMAC_SHA256(pubkey, scriptPubKey) as a
// tweak to each pubkey, producing the per-claim-script "contract" script. This is
// a pure script operation (no chain state) and lives in the common library so that
// both consensus/wallet code and the descriptor engine can reach it.
CScript calculate_contract(const CScript& federationRedeemScript, const CScript& witnessProgram);

// Returns true if the script matches the Liquid v1 watchman template (emergency
// keys must not be tweaked by calculate_contract).
bool MatchLiquidWatchman(const CScript& script);

#endif // BITCOIN_SCRIPT_PEGINS_H
