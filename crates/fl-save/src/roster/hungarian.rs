//! Maximum-weight assignment (Hungarian algorithm).

const UNSET: i64 = i64::MAX / 2;

/// Assign each row a distinct column maximising the total weight.
///
/// `None` marks a forbidden pair. Returns the column per row, or `None` when
/// there are fewer columns than rows or no assignment avoids every forbidden
/// pair. O(rows² · columns). All arithmetic is exact integer arithmetic.
pub(crate) fn max_weight_assignment(weights: &[Vec<Option<i64>>]) -> Option<Vec<usize>> {
    let rows = weights.len();
    if rows == 0 {
        return Some(Vec::new());
    }
    let columns = weights[0].len();
    if columns < rows {
        return None;
    }
    let finite = || weights.iter().flatten().flatten().copied();
    let highest = finite().max()?;
    let lowest = finite().min()?;
    // Shift costs to [0, spread]; a forbidden pair then costs more than any
    // whole assignment of allowed pairs.
    let forbidden = rows as i64 * (highest - lowest) + 1;
    let cost = |row: usize, column: usize| weights[row][column].map_or(forbidden, |w| highest - w);

    let mut u = vec![0i64; rows + 1];
    let mut v = vec![0i64; columns + 1];
    // owner[j]: 1-based row matched to column j (0 = none).
    let mut owner = vec![0usize; columns + 1];
    let mut way = vec![0usize; columns + 1];
    for row in 1..=rows {
        owner[0] = row;
        let mut current = 0usize;
        let mut min_reduced = vec![UNSET; columns + 1];
        let mut used = vec![false; columns + 1];
        loop {
            used[current] = true;
            let row0 = owner[current];
            let mut delta = UNSET;
            let mut next = 0usize;
            for column in 1..=columns {
                if used[column] {
                    continue;
                }
                let reduced = cost(row0 - 1, column - 1) - u[row0] - v[column];
                if reduced < min_reduced[column] {
                    min_reduced[column] = reduced;
                    way[column] = current;
                }
                if min_reduced[column] < delta {
                    delta = min_reduced[column];
                    next = column;
                }
            }
            for column in 0..=columns {
                if used[column] {
                    u[owner[column]] += delta;
                    v[column] -= delta;
                } else {
                    min_reduced[column] -= delta;
                }
            }
            current = next;
            if owner[current] == 0 {
                break;
            }
        }
        while current != 0 {
            let previous = way[current];
            owner[current] = owner[previous];
            current = previous;
        }
    }

    let mut assignment = vec![usize::MAX; rows];
    for column in 1..=columns {
        if owner[column] != 0 {
            assignment[owner[column] - 1] = column - 1;
        }
    }
    if assignment
        .iter()
        .enumerate()
        .any(|(row, &column)| weights[row][column].is_none())
    {
        return None;
    }
    Some(assignment)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn picks_the_maximum_total_not_the_greedy_row_order() {
        // Greedy would give row 0 its best column (0) and leave 1 + 0.
        let weights = vec![vec![Some(10), Some(9)], vec![Some(10), Some(0)]];
        assert_eq!(max_weight_assignment(&weights), Some(vec![1, 0]));
    }

    #[test]
    fn forbidden_pairs_are_avoided_or_reported() {
        let weights = vec![vec![None, Some(1)], vec![Some(5), None]];
        assert_eq!(max_weight_assignment(&weights), Some(vec![1, 0]));
        let blocked = vec![vec![None, Some(1)], vec![None, Some(2)]];
        assert_eq!(max_weight_assignment(&blocked), None);
        assert_eq!(max_weight_assignment(&[vec![None]]), None);
    }

    #[test]
    fn degenerate_shapes() {
        assert_eq!(max_weight_assignment(&[]), Some(Vec::new()));
        assert_eq!(max_weight_assignment(&[vec![Some(1)], vec![Some(2)]]), None);
    }
}
