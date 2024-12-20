#include "schnorr0.h"

/* A length-prefixed encoding of the following Simplicity program:
 *     (scribe (toWord256 0xF9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9) &&&
 *      zero word256) &&&
 *      witness (toWord512 0xE907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA821525F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0) >>>
 *     Simplicity.Programs.LibSecp256k1.Lib.bip_0340_verify
 * with jets.
 */
const unsigned char schnorr0[] = {
  0xc6, 0xd5, 0xf2, 0x61, 0x14, 0x03, 0x24, 0xb1, 0x86, 0x20, 0x92, 0x68, 0x9f, 0x0b, 0xf1, 0x3a, 0xa4, 0x53, 0x6a, 0x63,
  0x90, 0x8b, 0x06, 0xdf, 0x33, 0x61, 0x0c, 0x03, 0xe2, 0x27, 0x79, 0xc0, 0x6d, 0xf2, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xe2, 0x8d, 0x8c, 0x04, 0x00
};

const size_t sizeof_schnorr0 = sizeof(schnorr0);
const unsigned char schnorr0_witness[] = {
  0xe9, 0x07, 0x83, 0x1f, 0x80, 0x84, 0x8d, 0x10, 0x69, 0xa5, 0x37, 0x1b, 0x40, 0x24, 0x10, 0x36, 0x4b, 0xdf, 0x1c, 0x5f,
  0x83, 0x07, 0xb0, 0x08, 0x4c, 0x55, 0xf1, 0xce, 0x2d, 0xca, 0x82, 0x15, 0x25, 0xf6, 0x6a, 0x4a, 0x85, 0xea, 0x8b, 0x71,
  0xe4, 0x82, 0xa7, 0x4f, 0x38, 0x2d, 0x2c, 0xe5, 0xeb, 0xee, 0xe8, 0xfd, 0xb2, 0x17, 0x2f, 0x47, 0x7d, 0xf4, 0x90, 0x0d,
  0x31, 0x05, 0x36, 0xc0
};

const size_t sizeof_schnorr0_witness = sizeof(schnorr0_witness);

/* The commitment Merkle root of the above schnorr0 Simplicity expression. */
const uint32_t schnorr0_cmr[] = {
  0x8a9e9767u, 0x6b24be77u, 0x97d9ee0bu, 0xf32dd76bu, 0xcd78028eu, 0x973025f7u, 0x85eae8dcu, 0x91c8a0dau
};

/* The identity Merkle root of the above schnorr0 Simplicity expression. */
const uint32_t schnorr0_imr[] = {
  0xad7c38b1u, 0x6b912964u, 0x6dc89b52u, 0xcff144deu, 0x94a80e38u, 0x3c4983b5u, 0x3de65e35u, 0x75abcf38u
};

/* The annotated Merkle root of the above schnorr0 Simplicity expression. */
const uint32_t schnorr0_amr[] = {
  0xec97c877u, 0x4cb6bfb3u, 0x81fdbbccu, 0x8d964380u, 0xfb3a3b45u, 0x77932262u, 0x4490d623u, 0x1ae777a4u
};

/* The cost of the above schnorr0 Simplicity expression in milli weight units. */
const ubounded schnorr0_cost = 51635;