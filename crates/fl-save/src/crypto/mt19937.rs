//! MT19937 (Matsumoto/Nishimura) with `init_by_array` seeding, exactly as in
//! the pesXdecrypter C implementation's `mt19937ar.c` (see THIRD_PARTY_NOTICES.md).

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

pub(super) struct Mt19937 {
    state: [u32; N],
    index: usize,
}

impl Mt19937 {
    /// Seed from a key array (`init_by_array`).
    pub(super) fn from_key(key: &[u32]) -> Self {
        debug_assert!(!key.is_empty());
        let mut mt = [0u32; N];
        mt[0] = 19_650_218;
        for i in 1..N {
            mt[i] = 1_812_433_253u32
                .wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }

        let mut i = 1;
        let mut j = 0;
        for _ in 0..N.max(key.len()) {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_566_083_941))
                .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
        }
        mt[0] = 0x8000_0000;

        Self {
            state: mt,
            index: N,
        }
    }

    fn twist(&mut self) {
        let mt = &mut self.state;
        let mag = |y: u32| if y & 1 == 0 { 0 } else { MATRIX_A };
        for kk in 0..N - M {
            let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + M] ^ (y >> 1) ^ mag(y);
        }
        for kk in N - M..N - 1 {
            let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + M - N] ^ (y >> 1) ^ mag(y);
        }
        let y = (mt[N - 1] & UPPER_MASK) | (mt[0] & LOWER_MASK);
        mt[N - 1] = mt[M - 1] ^ (y >> 1) ^ mag(y);
        self.index = 0;
    }

    pub(super) fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            self.twist();
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Reference output of `init_by_array({0x123, 0x234, 0x345, 0x456})`
    /// from the original mt19937ar.c test driver.
    #[test]
    fn matches_reference_vector() {
        let mut mt = Mt19937::from_key(&[0x123, 0x234, 0x345, 0x456]);
        let first: Vec<u32> = (0..5).map(|_| mt.next_u32()).collect();
        assert_eq!(
            first,
            [1067595299, 955945823, 477289528, 4107218783, 4228976476]
        );
    }
}
