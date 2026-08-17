//! Databricks SQL generation (IMPLEMENTATION_PLAN §8.3).
//!
//! Two hard rules, enforced here:
//!   1. **Identifiers** (catalog/schema/table/column) are never taken from
//!      free-form text. They come from metadata objects and are quoted with a
//!      dedicated backtick encoder that rejects/escapes embedded backticks.
//!   2. **Values** are never string-interpolated into SQL. They are emitted as
//!      named statement parameters (`:p0`, `:p1`, …) and sent separately, per
//!      Databricks' parameterized-query guidance.
//!
//! This module is the injection boundary for both the Phase 1 read-path spike
//! and the Phase 2 checkout SELECT.

use serde::{Deserialize, Serialize};

/// Quote a single SQL identifier with backticks, escaping any backtick by
/// doubling it. Rejects control characters and empty identifiers.
pub fn quote_ident(ident: &str) -> Result<String, SqlError> {
    if ident.is_empty() {
        return Err(SqlError::EmptyIdentifier);
    }
    if ident.chars().any(|c| c.is_control()) {
        return Err(SqlError::InvalidIdentifier(ident.to_string()));
    }
    Ok(format!("`{}`", ident.replace('`', "``")))
}

/// Quote a dotted name like `main.crm.customers` part-by-part.
pub fn quote_qualified(full_name: &str) -> Result<String, SqlError> {
    full_name
        .split('.')
        .map(quote_ident)
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("."))
}

/// A comparison operator supported by the MVP working-set filters (§7.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FilterOp {
    Eq,
    Ne,
    Gt,
    Lt,
    Before,
    After,
    Contains,
    InList,
    IsNull,
    IsNotNull,
}

impl FilterOp {
    pub fn parse(s: &str) -> Option<FilterOp> {
        Some(match s {
            "eq" | "equals" | "=" => FilterOp::Eq,
            "ne" | "not equals" | "!=" => FilterOp::Ne,
            "gt" | "greater than" | ">" => FilterOp::Gt,
            "lt" | "less than" | "<" => FilterOp::Lt,
            "before" => FilterOp::Before,
            "after" => FilterOp::After,
            "contains" => FilterOp::Contains,
            "in_list" | "in list" => FilterOp::InList,
            "is_null" | "is null" => FilterOp::IsNull,
            "is_not_null" | "is not null" => FilterOp::IsNotNull,
            _ => return None,
        })
    }
}

/// One filter predicate. `column` is an identifier (validated); `value` is a
/// user value that becomes a bound parameter.
#[derive(Debug, Clone)]
pub struct Predicate {
    pub column: String,
    pub op: FilterOp,
    pub value: String,
}

/// A bound statement parameter, matching the Statement Execution API shape.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StatementParam {
    pub name: String,
    pub value: String,
    #[serde(rename = "type")]
    pub type_name: String,
}

/// Result of compiling a working-set SELECT: the SQL text with `:pN`
/// placeholders and the ordered parameters to bind.
#[derive(Debug, Clone)]
pub struct CompiledQuery {
    pub sql: String,
    pub params: Vec<StatementParam>,
}

/// Build a projected + filtered `SELECT` for a checkout working set.
///
/// - `table` is a dotted qualified name from metadata.
/// - `columns` are identifiers from metadata (empty ⇒ `SELECT *`, discouraged).
/// - `predicates` become a parameterized `WHERE ... AND ...`.
pub fn build_working_set_select(
    table: &str,
    columns: &[String],
    predicates: &[Predicate],
) -> Result<CompiledQuery, SqlError> {
    let projection = if columns.is_empty() {
        "*".to_string()
    } else {
        columns
            .iter()
            .map(|c| quote_ident(c))
            .collect::<Result<Vec<_>, _>>()?
            .join(", ")
    };
    let qualified = quote_qualified(table)?;

    let mut sql = format!("SELECT {projection} FROM {qualified}");
    let mut params = Vec::new();

    if !predicates.is_empty() {
        let mut clauses = Vec::new();
        for (i, p) in predicates.iter().enumerate() {
            let col = quote_ident(&p.column)?;
            let pname = format!("p{i}");
            match p.op {
                FilterOp::IsNull => clauses.push(format!("{col} IS NULL")),
                FilterOp::IsNotNull => clauses.push(format!("{col} IS NOT NULL")),
                FilterOp::Contains => {
                    // LIKE with a bound value; caller supplies the raw substring,
                    // we wrap it with % in the parameter, not in the SQL text.
                    clauses.push(format!("{col} LIKE :{pname}"));
                    params.push(param(&pname, &format!("%{}%", p.value)));
                }
                FilterOp::InList => {
                    // Comma-separated list expands to individual bound params.
                    let items: Vec<&str> = p
                        .value
                        .split(',')
                        .map(|s| s.trim())
                        .filter(|s| !s.is_empty())
                        .collect();
                    if items.is_empty() {
                        return Err(SqlError::EmptyInList);
                    }
                    let mut placeholders = Vec::new();
                    for (j, item) in items.iter().enumerate() {
                        let n = format!("{pname}_{j}");
                        placeholders.push(format!(":{n}"));
                        params.push(param(&n, item));
                    }
                    clauses.push(format!("{col} IN ({})", placeholders.join(", ")));
                }
                op => {
                    let sql_op = match op {
                        FilterOp::Eq => "=",
                        FilterOp::Ne => "!=",
                        FilterOp::Gt | FilterOp::After => ">",
                        FilterOp::Lt | FilterOp::Before => "<",
                        _ => unreachable!(),
                    };
                    clauses.push(format!("{col} {sql_op} :{pname}"));
                    params.push(param(&pname, &p.value));
                }
            }
        }
        sql.push_str(" WHERE ");
        sql.push_str(&clauses.join(" AND "));
    }

    Ok(CompiledQuery { sql, params })
}

fn param(name: &str, value: &str) -> StatementParam {
    StatementParam {
        name: name.to_string(),
        value: value.to_string(),
        type_name: "STRING".to_string(),
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum SqlError {
    #[error("empty identifier")]
    EmptyIdentifier,
    #[error("invalid identifier: {0:?}")]
    InvalidIdentifier(String),
    #[error("IN list has no values")]
    EmptyInList,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quotes_and_escapes_identifiers() {
        assert_eq!(quote_ident("customer_id").unwrap(), "`customer_id`");
        // backtick-injection attempt is neutralized by doubling
        assert_eq!(quote_ident("a`b").unwrap(), "`a``b`");
        assert!(quote_ident("").is_err());
        assert!(quote_ident("bad\nname").is_err());
        assert_eq!(
            quote_qualified("main.crm.customers").unwrap(),
            "`main`.`crm`.`customers`"
        );
    }

    #[test]
    fn builds_parameterized_select() {
        let q = build_working_set_select(
            "main.crm.customers",
            &["customer_id".into(), "country".into()],
            &[
                Predicate {
                    column: "country".into(),
                    op: FilterOp::Eq,
                    value: "NL".into(),
                },
                Predicate {
                    column: "updated_at".into(),
                    op: FilterOp::After,
                    value: "2026-01-01".into(),
                },
            ],
        )
        .unwrap();
        assert_eq!(
            q.sql,
            "SELECT `customer_id`, `country` FROM `main`.`crm`.`customers` WHERE `country` = :p0 AND `updated_at` > :p1"
        );
        assert_eq!(q.params.len(), 2);
        assert_eq!(q.params[0], param("p0", "NL"));
        assert_eq!(q.params[1].value, "2026-01-01");
        // no raw value ever appears in the SQL text
        assert!(!q.sql.contains("NL"));
    }

    #[test]
    fn contains_and_null_and_in_list() {
        let q = build_working_set_select(
            "t",
            &[],
            &[
                Predicate {
                    column: "name".into(),
                    op: FilterOp::Contains,
                    value: "aco".into(),
                },
                Predicate {
                    column: "email".into(),
                    op: FilterOp::IsNotNull,
                    value: String::new(),
                },
                Predicate {
                    column: "region".into(),
                    op: FilterOp::InList,
                    value: "EMEA, APAC".into(),
                },
            ],
        )
        .unwrap();
        assert_eq!(
            q.sql,
            "SELECT * FROM `t` WHERE `name` LIKE :p0 AND `email` IS NOT NULL AND `region` IN (:p2_0, :p2_1)"
        );
        assert_eq!(q.params[0].value, "%aco%");
        assert_eq!(q.params[1].value, "EMEA");
        assert_eq!(q.params[2].value, "APAC");
    }

    #[test]
    fn injection_in_value_stays_in_params() {
        let q = build_working_set_select(
            "t",
            &["c".into()],
            &[Predicate {
                column: "c".into(),
                op: FilterOp::Eq,
                value: "x'; DROP TABLE t;--".into(),
            }],
        )
        .unwrap();
        assert!(!q.sql.contains("DROP TABLE"));
        assert_eq!(q.params[0].value, "x'; DROP TABLE t;--");
    }
}
