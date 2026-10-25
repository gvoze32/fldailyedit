//! Lenient JSON accessors: a malformed provider field degrades to "missing"
//! instead of failing a scrape. Coercion rules: empty values (`null`, `0`,
//! `""`, `[]`, `{}`, `false`) are falsy, integers parse from numbers and
//! trimmed strings, and anything unparsable becomes `None` (or the documented
//! default).

use serde_json::Value;

/// Whether a JSON value is non-empty (`null`/`0`/`""`/`[]`/`{}`/`false` are falsy).
pub fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_none_or(|n| n != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Display text of a scalar: `null` is `None`, booleans are `True`/`False`,
/// strings are verbatim, and numbers and containers use their JSON text.
pub fn display_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// Display text of the value, or `""` when it is absent or falsy.
pub fn str_or_empty(value: Option<&Value>) -> String {
    match value {
        Some(value) if is_truthy(value) => display_str(value),
        _ => String::new(),
    }
}

/// [`str_or_empty`] with surrounding whitespace removed.
pub fn trimmed(value: Option<&Value>) -> String {
    str_or_empty(value).trim().to_owned()
}

/// Integer value where one can be read, else `None`. Floats truncate toward
/// zero; strings may carry whitespace, a sign and single underscores between
/// digits; booleans are 0/1.
pub fn lenient_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(flag) => Some(i64::from(*flag)),
        Value::Number(number) => number.as_i64().or_else(|| {
            let float = number.as_f64()?;
            // Values beyond i64 are rejected; ids never get there.
            (float.is_finite() && float.abs() < 9.2e18).then(|| float.trunc() as i64)
        }),
        Value::String(text) => parse_int_text(text),
        _ => None,
    }
}

fn parse_int_text(text: &str) -> Option<i64> {
    let text = text.trim();
    let (negative, digits) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
    {
        return None;
    }
    let cleaned: String = digits.chars().filter(|c| *c != '_').collect();
    if !cleaned.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let magnitude: i64 = cleaned.parse().ok()?;
    Some(if negative { -magnitude } else { magnitude })
}

/// A strictly positive integer or `None`.
pub fn optional_positive_int(value: Option<&Value>) -> Option<i64> {
    value.and_then(lenient_int).filter(|n| *n > 0)
}

/// Integer value of a truthy value, with absent, falsy and unparsable values
/// mapped to 0.
pub fn int_or_zero(value: Option<&Value>) -> i64 {
    match value {
        Some(value) if is_truthy(value) => lenient_int(value).unwrap_or(0),
        _ => 0,
    }
}

/// `int_or_zero` clamped into the unsigned age/shirt range.
pub fn non_negative_u32(value: i64) -> u32 {
    u32::try_from(value.max(0)).unwrap_or(u32::MAX)
}

/// A finite floating-point number from a number or numeric string; booleans
/// are rejected.
pub fn finite_number(value: Option<&Value>) -> Option<f64> {
    let number = match value? {
        Value::Bool(_) => return None,
        Value::Number(number) => number.as_f64()?,
        Value::String(text) => text.trim().parse::<f64>().ok()?,
        _ => return None,
    };
    number.is_finite().then_some(number)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn int_coercion() {
        assert_eq!(lenient_int(&json!(5)), Some(5));
        assert_eq!(lenient_int(&json!(5.9)), Some(5));
        assert_eq!(lenient_int(&json!(-5.9)), Some(-5));
        assert_eq!(lenient_int(&json!(" 12 ")), Some(12));
        assert_eq!(lenient_int(&json!("1_000")), Some(1000));
        assert_eq!(lenient_int(&json!("1__0")), None);
        assert_eq!(lenient_int(&json!("12.0")), None);
        assert_eq!(lenient_int(&json!("")), None);
        assert_eq!(lenient_int(&json!(null)), None);
        assert_eq!(lenient_int(&json!(true)), Some(1));
        assert_eq!(lenient_int(&json!({})), None);
        assert_eq!(optional_positive_int(Some(&json!(0))), None);
        assert_eq!(optional_positive_int(Some(&json!("7"))), Some(7));
        assert_eq!(int_or_zero(Some(&json!("abc"))), 0);
        assert_eq!(int_or_zero(Some(&json!(null))), 0);
        assert_eq!(int_or_zero(None), 0);
    }

    #[test]
    fn str_coercion() {
        assert_eq!(str_or_empty(Some(&json!(null))), "");
        assert_eq!(str_or_empty(Some(&json!(0))), "");
        assert_eq!(str_or_empty(Some(&json!(12))), "12");
        assert_eq!(str_or_empty(Some(&json!(true))), "True");
        assert_eq!(trimmed(Some(&json!("  x "))), "x");
    }

    #[test]
    fn float_coercion() {
        assert_eq!(finite_number(Some(&json!("1.5"))), Some(1.5));
        assert_eq!(finite_number(Some(&json!(true))), None);
        assert_eq!(finite_number(Some(&json!("nan"))), None);
        assert_eq!(finite_number(Some(&json!(3))), Some(3.0));
    }
}
