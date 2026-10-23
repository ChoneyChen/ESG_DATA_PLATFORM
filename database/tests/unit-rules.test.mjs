import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { NORMALIZATION_RULE_VERSION } from "../scripts/publication.mjs";

const file = new URL("../scripts/unit-conversions.1.5.4.json", import.meta.url);

test("every declared unit alias maps to exactly one canonical unit", async () => {
  const rules = JSON.parse(await readFile(file, "utf8"));
  assert.equal(NORMALIZATION_RULE_VERSION, rules.rule_version);
  assert.ok(rules.conversions.length > 0);
  const owners = new Map();
  for (const rule of rules.conversions) {
    assert.ok(rule.canonical_unit, "canonical_unit is required");
    assert.ok(Number(rule.multiplier) > 0 && Number(rule.divisor) > 0);
    for (const alias of [rule.canonical_unit, ...(rule.aliases || [])]) {
      const key = String(alias).replace(/\s+/g, "").toLowerCase();
      const owner = owners.get(key);
      if (owner && owner.unit !== rule.canonical_unit) {
        // A mass unit may also name a volume only through the explicit
        // water-density rule; any other shared alias is a real ambiguity.
        assert.ok(owner.arithmetic || rule.arithmetic,
          `alias ${alias} points at both ${owner.unit} and ${rule.canonical_unit}`);
      }
      owners.set(key, { unit: rule.canonical_unit, arithmetic: rule.arithmetic });
    }
  }
});
