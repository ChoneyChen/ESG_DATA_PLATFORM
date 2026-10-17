import assert from "node:assert/strict";

import { applyMigrations, openDatabase } from "./db.mjs";

const db = await openDatabase();

try {
  await applyMigrations(db);

  const invalidApproved = await db.query(`
    SELECT count(*)::integer AS count
    FROM esg.esg_disclosures d
    LEFT JOIN esg.disclosure_sources s
      ON s.disclosure_id = d.disclosure_id
    WHERE d.quality_status = 'approved'
    GROUP BY d.disclosure_id
    HAVING count(s.disclosure_source_id) = 0
  `);
  assert.equal(
    invalidApproved.rows.length,
    0,
    "Every approved disclosure must have at least one simplified source.",
  );

  const mappedWithoutConcept = await db.query(`
    SELECT count(*)::integer AS count
    FROM esg.esg_disclosures
    WHERE mapping_status = 'mapped' AND concept_id IS NULL
  `);
  assert.equal(mappedWithoutConcept.rows[0].count, 0);

  const malformedSubtype = await db.query(`
    SELECT count(*)::integer AS count
    FROM esg.esg_disclosures d
    LEFT JOIN esg.structured_values v
      ON v.disclosure_id = d.disclosure_id
    LEFT JOIN esg.qualitative_claims q
      ON q.disclosure_id = d.disclosure_id
    WHERE
      (
        d.disclosure_type IN ('quantitative', 'categorical')
        AND (v.disclosure_id IS NULL OR q.disclosure_id IS NOT NULL)
      )
      OR (
        d.disclosure_type = 'qualitative'
        AND (q.disclosure_id IS NULL OR v.disclosure_id IS NOT NULL)
      )
  `);
  assert.equal(
    malformedSubtype.rows[0].count,
    0,
    "Each disclosure must use exactly the correct result subtype.",
  );

  const comparisonRows = await db.query(`
    SELECT
      concept_code,
      normalized_numeric_value,
      dimensions,
      comparison_signature
    FROM esg_api.v_quantitative_observations
    ORDER BY normalized_numeric_value
  `);

  if (comparisonRows.rows.length > 0) {
    const signatures = new Set(
      comparisonRows.rows.map((row) => row.comparison_signature),
    );
    assert.equal(
      signatures.size,
      comparisonRows.rows.length,
      "Demo disclosures with different scopes must not collapse to one comparison signature.",
    );
  }

  const result = {
    checks: {
      approved_disclosures_have_sources: true,
      mapped_disclosures_have_concepts: true,
      result_subtypes_are_consistent: true,
      scope_specific_comparison_signatures_are_distinct: true,
    },
    quantitativeRowsChecked: comparisonRows.rows.length,
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} finally {
  await db.close();
}
