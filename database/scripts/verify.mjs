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

  // Published observations must keep at least one immutable source. Identity
  // columns on the legacy views are never written, so they are not checked.
  const publishedWithoutSource = await db.query(`
    SELECT count(*)::integer AS count
    FROM esg.curated_observations o
    LEFT JOIN esg.observation_sources s ON s.observation_id = o.observation_id
    WHERE s.observation_id IS NULL
  `);
  assert.equal(
    publishedWithoutSource.rows[0].count,
    0,
    "Every published observation must retain at least one source.",
  );

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

  const duplicateIdentities = await db.query(`
    SELECT identity_key, value_origin, count(*)::integer AS count
    FROM esg.curated_observations
    GROUP BY identity_key, value_origin
    HAVING count(*) > 1
  `);
  assert.equal(
    duplicateIdentities.rows.length,
    0,
    "One identity key must not silently hold two published values of the same origin.",
  );

  const result = {
    checks: {
      approved_disclosures_have_sources: true,
      published_observations_have_sources: true,
      observation_identity_is_unique: true,
      result_subtypes_are_consistent: true,
    },
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} finally {
  await db.close();
}
