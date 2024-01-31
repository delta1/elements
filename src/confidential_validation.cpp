
#include <confidential_validation.h>
#include <issuance.h>
#include <pegins.h>
#include <script/sigcache.h>
#include <blind.h>

namespace {
static secp256k1_context *secp256k1_ctx_verify_amounts;

class CSecp256k1Init {
public:
    CSecp256k1Init() {
        assert(secp256k1_ctx_verify_amounts == NULL);
        secp256k1_ctx_verify_amounts = secp256k1_context_create(SECP256K1_CONTEXT_VERIFY | SECP256K1_CONTEXT_SIGN);
        assert(secp256k1_ctx_verify_amounts != NULL);
    }
    ~CSecp256k1Init() {
        assert(secp256k1_ctx_verify_amounts != NULL);
        secp256k1_context_destroy(secp256k1_ctx_verify_amounts);
        secp256k1_ctx_verify_amounts = NULL;
    }
};
static CSecp256k1Init instance_of_csecp256k1;
}

bool HasValidFee(const CTransaction& tx) {
    CAmountMap totalFee;
    for (unsigned int i = 0; i < tx.vout.size(); i++) {
        CAmount fee = 0;
        if (tx.vout[i].IsFee()) {
            fee = tx.vout[i].nValue.GetAmount();
            if (fee == 0 || !MoneyRange(fee))
                return false;
            totalFee[tx.vout[i].nAsset.GetAsset()] += fee;
            if (!MoneyRange(totalFee)) {
                return false;
            }
        }
    }
    return true;
}

CAmountMap GetFeeMap(const CTransaction& tx) {
    CAmountMap fee;
    for (const CTxOut& txout : tx.vout) {
        if (txout.IsFee()) {
            fee[txout.nAsset.GetAsset()] += txout.nValue.GetAmount();
        }
    }
    return fee;
}

bool CRangeCheck::operator()() {
    assert(val->IsCommitment());

    if (!CachingRangeProofChecker(store).VerifyRangeProof(rangeproof, val->vchCommitment, assetCommitment, scriptPubKey, secp256k1_ctx_verify_amounts)) {
        error = SCRIPT_ERR_RANGEPROOF;
        return false;
    }

    return true;
};

bool CBalanceCheck::operator()() {
    if (!secp256k1_pedersen_verify_tally(secp256k1_ctx_verify_amounts, vpCommitsIn.data(), vpCommitsIn.size(), vpCommitsOut.data(), vpCommitsOut.size())) {
        error = SCRIPT_ERR_PEDERSEN_TALLY;
        LogPrintf("cbalancecheck error: %d\n", error);
        return false;
    }

    return true;
}

bool CSurjectionCheck::operator()() {
    return CachingSurjectionProofChecker(store).VerifySurjectionProof(proof, vTags, gen, secp256k1_ctx_verify_amounts, wtxid);
}

// Destroys the check in the case of no queue, or passes its ownership to the queue.
ScriptError QueueCheck(std::vector<CCheck*>* queue, CCheck* check) {
    if (queue != NULL) {
        queue->push_back(check);
        return SCRIPT_ERR_OK;
    }
    bool success = (*check)();
    ScriptError err = check->GetScriptError();
    delete check;
    return success ? SCRIPT_ERR_OK : err;
}

// Helper function for VerifyAmount(), not exported
static bool VerifyIssuanceAmount(secp256k1_pedersen_commitment& value_commit, secp256k1_generator& asset_gen,
                    const CAsset& asset, const CConfidentialValue& value, const std::vector<unsigned char>& rangeproof,
                    std::vector<CCheck*>* checks, const bool store_result)
{
    // This is used to add in the explicit values
    unsigned char explicit_blinds[32];
    memset(explicit_blinds, 0, sizeof(explicit_blinds));
    int ret;

    ret = secp256k1_generator_generate(secp256k1_ctx_verify_amounts, &asset_gen, asset.begin());
    assert(ret == 1);

    // Build value commitment
    if (value.IsExplicit()) {
        if (!MoneyRange(value.GetAmount()) || value.GetAmount() == 0) {
            return false;
        }
        if (!rangeproof.empty()) {
            return false;
        }


        ret = secp256k1_pedersen_commit(secp256k1_ctx_verify_amounts, &value_commit, explicit_blinds, value.GetAmount(), &asset_gen);
        // The explicit_blinds are all 0, and the amount is not 0. So secp256k1_pedersen_commit does not fail.
        assert(ret == 1);
    } else if (value.IsCommitment()) {
        // Verify range proof
        std::vector<unsigned char> vchAssetCommitment(CConfidentialAsset::nExplicitSize);
        secp256k1_generator_serialize(secp256k1_ctx_verify_amounts, vchAssetCommitment.data(), &asset_gen);
        if (QueueCheck(checks, new CRangeCheck(&value, rangeproof, vchAssetCommitment, CScript(), store_result)) != SCRIPT_ERR_OK) {
            return false;
        }

        if (secp256k1_pedersen_commitment_parse(secp256k1_ctx_verify_amounts, &value_commit, value.vchCommitment.data()) != 1) {
            return false;
        }
    } else {
        return false;
    }

    return true;
}

static void log_hex(unsigned char* data, size_t size) {
    size_t i;
    LogPrintf("commit_hex: ");
    for (i = 0; i < size; i++) {
        LogPrintf("%02x", data[i]);
    }
    LogPrintf("\n");
}

bool VerifyAmounts(const std::vector<CTxOut>& inputs, const CTransaction& tx, std::vector<CCheck*>* checks, const bool store_result) {
    LogPrintf("VerifyAmounts\n");
    LogPrintf("checks == nullptr? %d\n", checks == nullptr);
    assert(!tx.IsCoinBase());
    assert(inputs.size() == tx.vin.size());

    std::vector<secp256k1_pedersen_commitment> vData;
    std::vector<secp256k1_pedersen_commitment*> vpCommitsIn, vpCommitsOut;

    auto num = GetNumIssuances(tx);
    LogPrintf("vin: %d vout: %d num_iss: %d\n", tx.vin.size(), tx.vout.size(), num);
    vData.reserve((tx.vin.size() + tx.vout.size() + num));
    secp256k1_pedersen_commitment *p = vData.data();
    secp256k1_pedersen_commitment commit;
    secp256k1_generator gen;
    // This is used to add in the explicit values
    unsigned char explicit_blinds[32] = {0};
    int ret;

    uint256 wtxid(tx.GetWitnessHash());

    // This list is used to verify surjection proofs.
    // Proofs must be constructed with the list being in
    // order of input and non-null issuance pseudo-inputs, with
    // input first, asset issuance second, reissuance token third.
    std::vector<secp256k1_generator> target_generators;
    target_generators.reserve(tx.vin.size() + GetNumIssuances(tx));

    // Tally up value commitments, check balance
    for (size_t i = 0; i < tx.vin.size(); ++i) {
        const CConfidentialValue& val = inputs[i].nValue;
        const CConfidentialAsset& asset = inputs[i].nAsset;

        if (val.IsNull() || asset.IsNull()) {
            LogPrintf("valisnull\n");
            return false;
        }

        if (asset.IsExplicit()) {
            ret = secp256k1_generator_generate(secp256k1_ctx_verify_amounts, &gen, asset.GetAsset().begin());
            assert(ret != 0);
        }
        else if (asset.IsCommitment()) {
            if (secp256k1_generator_parse(secp256k1_ctx_verify_amounts, &gen, &asset.vchCommitment[0]) != 1) {
                LogPrintf("genparse\n");
                return false;
            }
        }
        else {
            LogPrintf("elsefalse\n");
            return false;
        }

        target_generators.push_back(gen);

        if (val.IsExplicit()) {
            LogPrintf("in val is explicit: %d\n", val.GetAmount());
            if (!MoneyRange(val.GetAmount()))
            {
                LogPrintf("moneyrange\n");
                return false;
            }

            // Fails if val.GetAmount() == 0
            if (secp256k1_pedersen_commit(secp256k1_ctx_verify_amounts, &commit, explicit_blinds, val.GetAmount(), &gen) != 1) {
                LogPrintf("pedcommit\n");
                return false;
            }
        } else if (val.IsCommitment()) {
            LogPrintf("in val is commitment: %s\n", val.GetHex());
            if (secp256k1_pedersen_commitment_parse(secp256k1_ctx_verify_amounts, &commit, &val.vchCommitment[0]) != 1) {
                LogPrintf("pedparse\n");
                return false;
            }
        } else {
                LogPrintf("else2\n");
                return false;
        }

        LogPrintf("input commitment\n");
        log_hex(commit.data, sizeof(commit.data));
        vData.push_back(commit);
        vpCommitsIn.push_back(p);
        p++;

        // Each transaction input may have up to two "pseudo-inputs" to add to the LHS
        // for (re)issuance and may require up to two rangeproof checks:
        // blinded value of the new assets being made
        // blinded value of the issuance tokens being made (only for initial issuance)
        const CAssetIssuance& issuance = tx.vin[i].assetIssuance;

        // No issuances to process, continue to next input
        if (issuance.IsNull()) {
            continue;
        }

        CAsset assetID;
        CAsset assetTokenID;

        // First construct the assets of the issuances and reissuance token
        // These are calculated differently depending on if initial issuance or followup

        // New issuance, compute the asset ids
        if (issuance.assetBlindingNonce.IsNull()) {
            uint256 entropy;
            GenerateAssetEntropy(entropy, tx.vin[i].prevout, issuance.assetEntropy);
            CalculateAsset(assetID, entropy);
            // Null nAmount is considered explicit 0, so just check for commitment
            CalculateReissuanceToken(assetTokenID, entropy, issuance.nAmount.IsCommitment());
        } else {
        // Re-issuance
            // hashAssetIdentifier doubles as the entropy on reissuance
            CalculateAsset(assetID, issuance.assetEntropy);
            CalculateReissuanceToken(assetTokenID, issuance.assetEntropy, issuance.nAmount.IsCommitment());

            // Must check that prevout is the blinded issuance token
            // prevout's asset tag = assetTokenID + assetBlindingNonce
            if (secp256k1_generator_generate_blinded(secp256k1_ctx_verify_amounts, &gen, assetTokenID.begin(), issuance.assetBlindingNonce.begin()) != 1) {
                LogPrintf("genblind\n");
                return false;
            }
            // Serialize the generator for direct comparison
            unsigned char derived_generator[33];
            secp256k1_generator_serialize(secp256k1_ctx_verify_amounts, derived_generator, &gen);

            // Belt-and-suspenders: Check that asset commitment from issuance input is correct size
            if (asset.vchCommitment.size() != sizeof(derived_generator)) {
                LogPrintf("sizegen\n");
                return false;
            }

            // We have already checked the outputs' generator commitment for general validity, so directly compare serialized bytes
            if (memcmp(asset.vchCommitment.data(), derived_generator, sizeof(derived_generator))) {
                LogPrintf("comparebytes\n");
                return false;
            }
        }

        // Process issuance of asset
        if (!issuance.nAmount.IsValid()) {
                LogPrintf("isvalid\n");
            return false;
        }
        if (!issuance.nAmount.IsNull()) {
            // Note: This check disallows issuances in transactions with *no* witness data.
            // This can be relaxed in a future update as a HF by passing in an empty rangeproof
            // to `VerifyIssuanceAmount` instead.
            if (i >= tx.witness.vtxinwit.size()) {
                LogPrintf("witsize\n");
                return false;
            }
            if (!VerifyIssuanceAmount(commit, gen, assetID, issuance.nAmount, tx.witness.vtxinwit[i].vchIssuanceAmountRangeproof, checks, store_result)) {
                LogPrintf("verifyissuanceamount\n");
                return false;
            }
            target_generators.push_back(gen);
        LogPrintf("issuance namount commitment\n");
        log_hex(commit.data, sizeof(commit.data));
            vData.push_back(commit);
            vpCommitsIn.push_back(p);
            p++;
        }

        // Process issuance of reissuance tokens
        if (!issuance.nInflationKeys.IsValid()) {
                LogPrintf("inflationkeys\n");
            return false;
        }
        if (!issuance.nInflationKeys.IsNull()) {
            // Only initial issuance can have reissuance tokens
            if (!issuance.assetBlindingNonce.IsNull()) {
                LogPrintf("assetblindingnonce\n");
                return false;
            }

            // Note: This check disallows issuances in transactions with *no* witness data.
            // This can be relaxed in a future update as a HF by passing in an empty rangeproof
            // to `VerifyIssuanceAmount` instead.
            if (i >= tx.witness.vtxinwit.size()) {
                LogPrintf("witsize2\n");
                return false;
            }
            if (!VerifyIssuanceAmount(commit, gen, assetTokenID, issuance.nInflationKeys, tx.witness.vtxinwit[i].vchInflationKeysRangeproof, checks, store_result)) {
                LogPrintf("verifyissuance2\n");
                return false;
            }
            target_generators.push_back(gen);
        LogPrintf("issuance inflationkeys commitment\n");
        log_hex(commit.data, sizeof(commit.data));
            vData.push_back(commit);
            vpCommitsIn.push_back(p);
            p++;
        }
    }

    for (size_t i = 0; i < tx.vout.size(); ++i)
    {
        const CConfidentialValue& val = tx.vout[i].nValue;
        const CConfidentialAsset& asset = tx.vout[i].nAsset;
        if (!asset.IsValid())
        {
            LogPrintf("false\n");
            return false;
        }
        if (!val.IsValid())
        {
            LogPrintf("false\n");
            return false;
        }
        if (!tx.vout[i].nNonce.IsValid())
        {
                LogPrintf("false\n");
            return false;
        }

        if (asset.IsExplicit()) {
            ret = secp256k1_generator_generate(secp256k1_ctx_verify_amounts, &gen, asset.GetAsset().begin());
            assert(ret != 0);
        }
        else if (asset.IsCommitment()) {
            if (secp256k1_generator_parse(secp256k1_ctx_verify_amounts, &gen, &asset.vchCommitment[0]) != 1) {
                LogPrintf("false\n");
                return false;
            }
        }
        else {
                LogPrintf("false\n");
            return false;
        }

        if (val.IsExplicit()) {
            LogPrintf("out val is explicit: %d\n", val.GetAmount());
            if (!MoneyRange(val.GetAmount())) {
                LogPrintf("false\n");
                return false;
            }

            if (val.GetAmount() == 0) {
                if (tx.vout[i].scriptPubKey.IsUnspendable()) {
                    continue;
                } else {
                    // No spendable 0-value outputs
                    // Reason: A spendable output of 0 reissuance tokens would allow reissuance without reissuance tokens.
                LogPrintf("false\n");
                    return false;
                }
            }

            ret = secp256k1_pedersen_commit(secp256k1_ctx_verify_amounts, &commit, explicit_blinds, val.GetAmount(), &gen);
            // The explicit_blinds are all 0, and the amount is not 0. So secp256k1_pedersen_commit does not fail.
            assert(ret == 1);
        }
        else if (val.IsCommitment()) {
            LogPrintf("out val is commitment: %d\n", val.GetHex());
            if (secp256k1_pedersen_commitment_parse(secp256k1_ctx_verify_amounts, &commit, &val.vchCommitment[0]) != 1) {
                LogPrintf("false\n");
                return false;
            }
        } else {
                LogPrintf("false\n");
            return false;
        }

        LogPrintf("output commitment\n");
        log_hex(commit.data, sizeof(commit.data));
        vData.push_back(commit);
        vpCommitsOut.push_back(p);
        p++;
    }

    // Check balance
    LogPrintf("balancecheck in: %d out: %d vdata: %d\n", vpCommitsIn.size(), vpCommitsOut.size(), vData.size());
    if (QueueCheck(checks, new CBalanceCheck(vData, vpCommitsIn, vpCommitsOut)) != SCRIPT_ERR_OK) {
        LogPrintf("balancecheck\n");
        LogPrintf("balancecheck in: %d out: %d\n", vpCommitsIn.size(), vpCommitsOut.size());
        LogPrintf("vdata size: %d\n", vData.size());
        return false;
    }

    // Range proofs
    for (size_t i = 0; i < tx.vout.size(); i++) {
        const CConfidentialValue& val = tx.vout[i].nValue;
        const CConfidentialAsset& asset = tx.vout[i].nAsset;
        std::vector<unsigned char> vchAssetCommitment = asset.vchCommitment;
        const CTxOutWitness* ptxoutwit = tx.witness.vtxoutwit.size() <= i? NULL: &tx.witness.vtxoutwit[i];
        if (val.IsExplicit())
        {
            if (ptxoutwit && !ptxoutwit->vchRangeproof.empty())
            {
                LogPrintf("false\n");
                return false;
            }
            continue;
        }
        if (asset.IsExplicit()) {
            int ret = secp256k1_generator_generate(secp256k1_ctx_verify_amounts, &gen, asset.GetAsset().begin());
            assert(ret != 0);
            secp256k1_generator_serialize(secp256k1_ctx_verify_amounts, &vchAssetCommitment[0], &gen);
        }
        if (!ptxoutwit) {
                LogPrintf("false\n");
            return false;
        }
        if (QueueCheck(checks, new CRangeCheck(&val, ptxoutwit->vchRangeproof, vchAssetCommitment, tx.vout[i].scriptPubKey, store_result)) != SCRIPT_ERR_OK) {
                LogPrintf("false\n");
            return false;
        }
    }

    // Surjection proofs
    for (size_t i = 0; i < tx.vout.size(); i++)
    {
        const CConfidentialAsset& asset = tx.vout[i].nAsset;
        const CTxOutWitness* ptxoutwit = tx.witness.vtxoutwit.size() <= i? NULL: &tx.witness.vtxoutwit[i];
        // No need for surjection proof
        if (asset.IsExplicit()) {
            if (ptxoutwit && !ptxoutwit->vchSurjectionproof.empty()) {
                LogPrintf("false\n");
                return false;
            }
            continue;
        }
        if (!ptxoutwit) {
                LogPrintf("false\n");
            return false;
        }
        if (secp256k1_generator_parse(secp256k1_ctx_verify_amounts, &gen, &asset.vchCommitment[0]) != 1)
        {
                LogPrintf("false\n");
            return false;
        }

        secp256k1_surjectionproof proof;
        if (secp256k1_surjectionproof_parse(secp256k1_ctx_verify_amounts, &proof, &ptxoutwit->vchSurjectionproof[0], ptxoutwit->vchSurjectionproof.size()) != 1)
        {
                LogPrintf("false\n");
            return false;
        }

        if (QueueCheck(checks, new CSurjectionCheck(proof, target_generators, gen, wtxid, store_result)) != SCRIPT_ERR_OK) {
                LogPrintf("false\n");
            return false;
        }
    }

                LogPrintf("true\n");
    return true;
}

bool VerifyCoinbaseAmount(const CTransaction& tx, const CAmountMap& mapFees) {
    assert(tx.IsCoinBase());

    // Miner shouldn't be stuffing witness data
    for (const auto& outwit : tx.witness.vtxoutwit) {
        if (!outwit.IsNull()) {
            return false;
        }
    }

    CAmountMap remaining = mapFees;
    for (unsigned int i = 0; i < tx.vout.size(); i++) {
        const CTxOut& out = tx.vout[i];
        if (!out.nValue.IsExplicit() || !out.nAsset.IsExplicit()) {
            return false;
        }
        if (!MoneyRange(out.nValue.GetAmount())) {
            return false;
        }
        if (g_con_elementsmode &&
                out.nValue.GetAmount() == 0 && !out.scriptPubKey.IsUnspendable()) {
            return false;
        }
        remaining[out.nAsset.GetAsset()] -= out.nValue.GetAmount();
    }
    return MoneyRange(remaining);
}
