// Deterministic transform pipeline evaluation — a local stand-in for the
// DuckDB compilation described in the technical design. Ported from the
// prototype's Component logic.

import { RAW_ROWS, BASE_COLS, ACCT_MGRS, Row } from "./data";

export type Step = { id: string; type: string; summary: string; config?: any; inputId?: string | null };

export type Workspace = {
  name: string;
  version: number;
  changes: number;
  committed: boolean;
  rowCountLabel: string;
  sizeLabel: string;
  pipeline: Step[];
  /** Live checkout data (real rows from a governed SELECT). Absent ⇒ mock demo. */
  sourceRows?: Row[];
  sourceCols?: string[];
};

export type WorkingFilter = { column: string; op: string; value: string };

export type NewFlow = {
  tableName: string | null;
  columns: Record<string, boolean>;
  rowId: "single" | "composite";
  filters: WorkingFilter[];
  rowKeyCols: string[];
};

export function stepSummary(type: string): string {
  return (
    {
      select_columns: "6 of 8 columns",
      rename: "customer_company_name → company_name",
      formula: "margin = revenue - cost",
      manual_review: "3 cells edited",
    } as Record<string, string>
  )[type] || "Not configured";
}

export function buildExistingWorkspace(): Workspace {
  return {
    name: "Customers Cleanup",
    version: 481,
    changes: 32,
    committed: false,
    rowCountLabel: "1.2M",
    sizeLabel: "482 MB",
    pipeline: [
      { id: "s1", type: "source", summary: "main.crm.customers" },
      { id: "s2", type: "filter", summary: "country = Netherlands" },
      { id: "s3", type: "join_file", summary: "customer_mapping.xlsx · customer_id=customer_id" },
      { id: "s4", type: "deduplicate", summary: "key: email · keep latest" },
      { id: "s5", type: "replace", summary: "Netherland, NL, Nederland → Netherlands" },
      { id: "s6", type: "validate", summary: "Not run" },
    ],
  };
}

export function buildNewWorkspace(nf: NewFlow, live?: { rows: Row[]; cols: string[] }): Workspace {
  return {
    name: nf.tableName ? nf.tableName.split(".").pop() + " Workspace" : "New Workspace",
    version: 481,
    changes: 0,
    committed: false,
    rowCountLabel: live ? live.rows.length.toLocaleString("en-US") : "428.2K",
    sizeLabel: live ? "local" : "182 MB",
    pipeline: [{ id: "ns1", type: "source", summary: nf.tableName || "main.crm.customers" }],
    sourceRows: live?.rows,
    sourceCols: live?.cols,
  };
}

export function computeRows(
  pipelineSlice: Step[],
  sourceRows?: Row[],
  sourceCols?: string[]
): { rows: Row[]; cols: string[] } {
  let rows: Row[] = (sourceRows ?? RAW_ROWS).map((r) => ({ ...r }));
  let cols = [...(sourceCols ?? BASE_COLS)];
  for (const step of pipelineSlice) {
    if (step.type === "filter") {
      rows = rows.filter((r) => r.country !== "DE");
    } else if (step.type === "join_file") {
      rows = rows.map((r, i) => ({ ...r, region: "EMEA", account_manager: ACCT_MGRS[i % ACCT_MGRS.length] }));
      cols = [...cols, "region", "account_manager"];
    } else if (step.type === "deduplicate") {
      const seen: Record<string, boolean> = {};
      rows = rows.filter((r) => {
        if (seen[r.customer_id]) return false;
        seen[r.customer_id] = true;
        return true;
      });
    } else if (step.type === "replace") {
      const map: Record<string, string> = { NL: "Netherlands", Netherland: "Netherlands", Nederland: "Netherlands" };
      rows = rows.map((r) => ({ ...r, country: map[r.country] || r.country }));
    } else if (step.type === "formula") {
      rows = rows.map((r) => ({ ...r, margin: r.revenue - r.cost }));
      cols = [...cols, "margin"];
    }
  }
  return { rows, cols };
}

export function computeValidation(rules: Record<string, boolean> | undefined): {
  validCount: number;
  invalidCount: number;
} {
  let invalid = 0;
  for (const r of RAW_ROWS) {
    if (rules && rules.email_at && (!r.email || !r.email.includes("@"))) invalid++;
  }
  return { validCount: RAW_ROWS.length - invalid, invalidCount: invalid };
}
